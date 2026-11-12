'use strict';
// Turns a listing plus comparable prices into a fair value, the real economics
// of flipping it (fees and shipping both ways), and a 0-1 deal score.
const { median, trimOutliers, clamp } = require('../core/indicators');
const { findPhrases, hasPhrase, isLot, sameItem } = require('./text');

const ASKING_HAIRCUT = 0.8; // asking prices overstate what things sell for
const SOURCE_BUY_FEES = { shopgoodwill: 4, ebay: 0, demo: 0 }; // handling fees

function brandTier(text, cat) {
  for (const [tier, brands] of Object.entries(cat.brand_tiers || {})) {
    const hit = (brands || []).find((b) => hasPhrase(text, b));
    if (hit) return { tier, brand: hit };
  }
  return { tier: null, brand: null };
}

// Cheap rule checks that run before any comparable lookup.
function prefilter(listing, cat) {
  const text = `${listing.title} ${listing.description || ''}`;
  const flags = cat.red_flags || {};
  const critical = findPhrases(text, flags.critical);
  if (critical.length) return { ok: false, reason: `Rejected: "${critical[0]}"` };
  if (listing.price == null) return { ok: false, reason: 'No price shown' };
  if (listing.price < (cat.price_floor || 0)) return { ok: false, reason: 'Below category price floor' };
  if (listing.price > (cat.price_ceiling || Infinity)) return { ok: false, reason: 'Above category price ceiling' };
  return { ok: true };
}

// comps: [{title, price, kind: 'sold' | 'asking'}]
function fairValue(listing, cat, comps) {
  const flags = cat.red_flags || {};
  const junk = [...(flags.critical || []), ...(flags.major || [])];
  const usable = comps.filter(
    (c) => c.price > 0 && sameItem(listing.title, c.title) && !isLot(c.title) && !findPhrases(c.title, junk).length
  );
  const sold = usable.filter((c) => c.kind === 'sold');
  const asking = usable.filter((c) => c.kind === 'asking');
  // Real sold prices win; asking prices are a weaker fallback.
  const basis = sold.length >= 4 ? sold : asking.length >= 4 ? asking : null;
  if (!basis) return { fair: null, confidence: 'none', compCount: usable.length, basis: null };

  const prices = trimOutliers(basis.map((c) => c.price));
  const kind = basis === sold ? 'sold' : 'asking';
  const mid = median(prices);
  const fair = kind === 'sold' ? mid : mid * ASKING_HAIRCUT;
  const confidence = kind === 'asking' ? 'low' : prices.length >= 8 ? 'high' : 'medium';
  return {
    fair: Math.round(fair * 100) / 100,
    confidence,
    compCount: prices.length,
    basis: kind,
    low: prices[0],
    high: prices[prices.length - 1],
  };
}

function economics(listing, cat, fair, settings) {
  const feePct = cat.fee_pct ?? 0.13;
  const shipOut = cat.ship_est ?? 15;
  const shipIn = listing.shipping != null ? listing.shipping : shipOut;
  const buyFee = SOURCE_BUY_FEES[listing.source] || 0;
  const netResale = fair * (1 - feePct) - shipOut;
  const cost = listing.price + shipIn + buyFee;
  const netProfit = netResale - cost;
  const targetRoi = settings.targetRoi ?? 0.5;
  // Highest price at which the flip still earns the target return.
  const maxPrice = netResale / (1 + targetRoi) - shipIn - buyFee;
  return {
    cost: round2(cost),
    netProfit: round2(netProfit),
    roi: cost > 0 ? round2(netProfit / cost) : 0,
    discount: round2(1 - listing.price / fair),
    maxPrice: round2(Math.max(0, maxPrice)),
    shipIn: round2(shipIn),
    feePct,
  };
}

const round2 = (n) => Math.round(n * 100) / 100;
const CONF_WEIGHT = { high: 1, medium: 0.9, low: 0.75 };

function evaluate(listing, cat, comps, settings) {
  const pre = prefilter(listing, cat);
  if (!pre.ok) return { status: 'rejected', reason: pre.reason, score: 0 };

  const text = `${listing.title} ${listing.description || ''}`;
  const flags = cat.red_flags || {};
  const major = findPhrases(text, flags.major);
  const minor = findPhrases(text, flags.minor);
  const boosters = findPhrases(text, cat.value_boosters);
  const { tier, brand } = brandTier(text, cat);
  const base = { flags: { major, minor }, boosters, brand, tier };

  if (isLot(listing.title)) {
    return { ...base, status: 'unpriced', reason: 'Mixed lot: needs a human to value it', score: 0 };
  }
  const fv = fairValue(listing, cat, comps);
  if (fv.fair == null) {
    return { ...base, ...fv, status: 'unpriced', reason: `Only ${fv.compCount} comparable sales found`, score: 0 };
  }

  const eco = economics(listing, cat, fv.fair, settings);
  let score = 0.6 * clamp(eco.discount / 0.6) + 0.4 * clamp(eco.roi / 1.0);
  score += Math.min(boosters.length, 3) * 0.03;
  score -= major.length * 0.15 + minor.length * 0.05;
  if (listing.sellerRating != null && listing.sellerRating < 95) score -= 0.08;
  score += { tier_1: 0.06, tier_2: 0.03 }[tier] || 0;
  score = round2(clamp(score) * CONF_WEIGHT[fv.confidence]);

  const threshold = settings.alertThreshold ?? 0.55;
  const minProfit = settings.minProfit ?? 25;
  const good = score >= threshold && eco.netProfit >= minProfit;
  let reason = good ? 'Meets your score and profit thresholds' : 'Scored, below your thresholds';
  if (!good && eco.netProfit < minProfit && score >= threshold) reason = `Profit after fees under $${minProfit}`;
  return { ...base, ...fv, ...eco, status: good ? 'inbox' : 'scored', reason, score };
}

module.exports = { evaluate, fairValue, economics, prefilter, brandTier };
