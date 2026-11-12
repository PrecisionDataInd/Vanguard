'use strict';
// One deal scan: search each enabled source, value every new listing, and put
// the ones worth a look into the inbox. Nothing here spends money.
const { sleep } = require('../core/http');
const { evaluate, brandTier } = require('./valuation');
const { compQuery } = require('./text');
const ai = require('./ai');

const SOURCES = {
  demo: require('./sources/demo'),
  shopgoodwill: require('./sources/shopgoodwill'),
  ebay: require('./sources/ebay'),
};

const TERMS_PER_SCAN = 3;
const COMP_TTL_MS = 24 * 3600e3;
const MAX_RECORDS = 4000;

function activeSources(ctx) {
  if (ctx.settings.demoMode) return [SOURCES.demo];
  const out = [];
  if (ctx.settings.sources.shopgoodwill) out.push(SOURCES.shopgoodwill);
  if (ctx.settings.sources.ebay && ctx.secrets.ebayClientId && ctx.secrets.ebayClientSecret) out.push(SOURCES.ebay);
  return out;
}

async function lookupComps(ctx, sources, listing, cat) {
  const deals = ctx.state.deals;
  const { brand } = brandTier(listing.title, cat);
  const q = compQuery(listing.title, brand);
  if (!q) return [];
  const key = `${listing.source === 'demo' ? listing.id : ''}|${q}`;
  const hit = deals.comps[key];
  if (hit && Date.now() - hit.at < COMP_TTL_MS) return hit.comps;
  const comps = [];
  for (const src of sources) {
    try {
      comps.push(...(await src.comps(ctx, q, listing)));
    } catch (err) {
      markHealth(ctx, src.name, false, err.message);
    }
  }
  deals.comps[key] = { at: Date.now(), comps: comps.slice(0, 80) };
  return comps;
}

function markHealth(ctx, name, ok, error) {
  ctx.state.deals.health[name] = { ok, at: Date.now(), error: ok ? null : error };
  if (!ok) ctx.log('warn', `${SOURCES[name]?.label || name}: ${error}`);
}

function prune(deals) {
  const now = Date.now();
  for (const [k, v] of Object.entries(deals.comps)) if (now - v.at > COMP_TTL_MS) delete deals.comps[k];
  const ids = Object.keys(deals.items);
  if (ids.length <= MAX_RECORDS) return;
  const passive = ids
    .filter((id) => ['rejected', 'scored', 'unpriced', 'skipped'].includes(deals.items[id].status))
    .sort((a, b) => deals.items[a].firstSeen - deals.items[b].firstSeen);
  for (const id of passive.slice(0, ids.length - MAX_RECORDS)) delete deals.items[id];
}

async function scanDeals(ctx) {
  const deals = ctx.state.deals;
  const sources = activeSources(ctx);
  const stats = { searched: 0, fresh: 0, inbox: 0, unpriced: 0, errors: 0 };
  if (!sources.length) {
    ctx.log('warn', 'Deal scan skipped: no source is switched on. Open Settings to enable one.');
    return stats;
  }

  for (const [catName, cat] of Object.entries(ctx.knowledge.categories)) {
    if (ctx.settings.categories[catName] === false) continue;
    const terms = cat.search_terms || [];
    const start = deals.cursor[catName] || 0;
    const batch = [];
    for (let i = 0; i < Math.min(TERMS_PER_SCAN, terms.length); i++) batch.push(terms[(start + i) % terms.length]);
    deals.cursor[catName] = (start + TERMS_PER_SCAN) % Math.max(terms.length, 1);

    for (const src of sources) {
      if (src.name !== 'demo' && cat.sources && !cat.sources.includes(src.name)) continue;
      for (const term of src.name === 'demo' ? batch.slice(0, 1) : batch) {
        let found = [];
        try {
          found = await src.search(ctx, term, catName, 40);
          markHealth(ctx, src.name, true);
        } catch (err) {
          stats.errors++;
          markHealth(ctx, src.name, false, err.message);
          continue;
        }
        stats.searched += found.length;
        for (const listing of found) {
          const known = deals.items[listing.id];
          if (known) {
            // Keep live auction prices current without re-valuing everything.
            if (known.listing && listing.price != null) known.listing.price = listing.price;
            continue;
          }
          stats.fresh++;
          const comps = await lookupComps(ctx, sources, listing, cat);
          const val = evaluate(listing, cat, comps, ctx.settings);
          const { demoSold, ...clean } = listing;
          const rec = { listing: clean, val, status: val.status, firstSeen: Date.now(), updatedAt: Date.now() };
          if (val.status === 'rejected') rec.listing = { id: clean.id, title: clean.title, category: catName, source: clean.source };

          if (val.status === 'inbox' && ctx.settings.ai?.provider && ctx.settings.ai.provider !== 'off') {
            try {
              rec.ai = await ai.review(ctx, listing, val, cat);
              if (rec.ai && rec.ai.rating <= 2) {
                rec.status = 'scored';
                rec.val.reason = `AI review flagged it: ${rec.ai.reason}`;
              }
            } catch (err) {
              ctx.log('warn', `AI review unavailable: ${err.message}`);
            }
          }
          deals.items[listing.id] = rec;
          if (rec.status === 'inbox') {
            stats.inbox++;
            ctx.notify(
              `Deal: ${clean.title.slice(0, 70)}\n$${clean.price} vs est. $${val.fair} | profit ~$${val.netProfit} | score ${Math.round(val.score * 100)}`,
              clean.url
            );
          }
          if (rec.status === 'unpriced') stats.unpriced++;
        }
        if (src.name !== 'demo') await sleep(700);
      }
    }
    ctx.emit();
  }

  // Wake snoozed deals whose time is up.
  for (const rec of Object.values(deals.items)) {
    if (rec.status === 'snoozed' && rec.snoozeUntil && Date.now() >= rec.snoozeUntil) rec.status = 'inbox';
  }
  deals.lastScan = Date.now();
  prune(deals);
  ctx.log(
    'info',
    `Deal scan: ${stats.searched} listings seen, ${stats.fresh} new, ${stats.inbox} sent to inbox, ${stats.unpriced} need a manual look` +
      (stats.errors ? `, ${stats.errors} source errors` : '')
  );
  return stats;
}

module.exports = { scanDeals, SOURCES, activeSources };
