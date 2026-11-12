'use strict';
// eBay Browse API (the supported replacement for the retired Finding API).
// Needs a free developer keyset: Client ID + Client Secret, production.
// Browse only returns ACTIVE listings, so its comparables are asking prices.
const { getJson } = require('../../core/http');

const TOKEN_URL = 'https://api.ebay.com/identity/v1/oauth2/token';
const SEARCH_URL = 'https://api.ebay.com/buy/browse/v1/item_summary/search';
let cached = { token: null, expires: 0, id: null };

async function getToken(ctx) {
  const { ebayClientId: id, ebayClientSecret: secret } = ctx.secrets;
  if (!id || !secret) throw new Error('eBay keys not set');
  if (cached.token && cached.id === id && Date.now() < cached.expires) return cached.token;
  const body = new URLSearchParams({
    grant_type: 'client_credentials',
    scope: 'https://api.ebay.com/oauth/api_scope',
  });
  const data = await getJson(ctx.fetch, TOKEN_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: `Basic ${Buffer.from(`${id}:${secret}`).toString('base64')}`,
    },
    body: body.toString(),
  });
  cached = { token: data.access_token, expires: Date.now() + (data.expires_in - 120) * 1000, id };
  return cached.token;
}

async function query(ctx, q, { limit = 40, sort } = {}) {
  const token = await getToken(ctx);
  const params = new URLSearchParams({ q, limit: String(limit), filter: 'itemLocationCountry:US' });
  if (sort) params.set('sort', sort);
  const data = await getJson(ctx.fetch, `${SEARCH_URL}?${params}`, {
    headers: { Authorization: `Bearer ${token}`, 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US' },
  });
  return data.itemSummaries || [];
}

const num = (v) => (v == null || v === '' ? null : Number(v));

function normalize(item, category) {
  const options = item.buyingOptions || [];
  const isAuction = options.includes('AUCTION');
  const price = isAuction ? num(item.currentBidPrice?.value) ?? num(item.price?.value) : num(item.price?.value);
  const ship = item.shippingOptions?.[0]?.shippingCost?.value;
  return {
    id: `ebay_${item.itemId}`,
    source: 'ebay',
    category,
    title: item.title || '',
    price,
    isAuction,
    buyNow: options.includes('FIXED_PRICE') ? num(item.price?.value) : null,
    bids: item.bidCount ?? null,
    endsAt: item.itemEndDate || null,
    url: item.itemWebUrl,
    image: item.image?.imageUrl || item.thumbnailImages?.[0]?.imageUrl || null,
    sellerRating: num(item.seller?.feedbackPercentage),
    shipping: ship != null ? Number(ship) : null,
    condition: item.condition || null,
    location: item.itemLocation?.postalCode || null,
  };
}

async function search(ctx, term, category, limit) {
  const items = await query(ctx, term, { limit, sort: 'newlyListed' });
  return items.map((i) => normalize(i, category)).filter((l) => l.url && l.title);
}

async function comps(ctx, q) {
  const items = await query(ctx, q, { limit: 50 });
  return items
    .filter((i) => (i.buyingOptions || []).includes('FIXED_PRICE'))
    .map((i) => ({ title: i.title || '', price: Number(i.price?.value) || 0, kind: 'asking' }));
}

module.exports = { name: 'ebay', label: 'eBay', needsKeys: true, search, comps };
