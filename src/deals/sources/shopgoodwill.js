'use strict';
// ShopGoodwill.com: the site's own search endpoint, no key needed.
// Closed auctions give real SOLD prices, which is what makes this source
// valuable for valuation. This is an unofficial endpoint and can change; the
// app reports its health on the Activity page.
const { getJson } = require('../../core/http');

const API = 'https://buyerapi.shopgoodwill.com/api/Search/ItemListing';

function payload(text, { closed = false, pageSize = 40 } = {}) {
  const d = new Date();
  return {
    isSize: false,
    isWeddingCatagory: 'false',
    isMultipleCategoryIds: false,
    isFromHeaderMenuTab: false,
    layout: '',
    searchText: text,
    selectedGroup: '',
    selectedCategoryIds: '',
    selectedSellerIds: '',
    lowPrice: '0',
    highPrice: '999999',
    searchBuyNowOnly: '',
    searchPickupOnly: 'false',
    searchNoPickupOnly: 'false',
    searchOneCentShippingOnly: 'false',
    searchDescriptions: 'false',
    searchClosedAuctions: closed ? 'true' : 'false',
    closedAuctionEndingDate: `${d.getMonth() + 1}/${d.getDate()}/${d.getFullYear()}`,
    closedAuctionDaysBack: '90',
    searchCanadaShipping: 'false',
    searchInternationalShippingOnly: 'false',
    sortColumn: closed ? '1' : '5',
    page: '1',
    pageSize: String(pageSize),
    sortDescending: 'true',
    savedSearchId: 0,
    useBuyerPrefs: 'true',
    searchUSOnlyShipping: 'false',
    categoryLevelNo: '1',
    categoryLevel: 1,
    categoryId: 0,
    partNumber: '',
    catIds: '',
  };
}

async function query(ctx, text, opts) {
  const data = await getJson(ctx.fetch, API, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vanguard/1.0',
    },
    body: JSON.stringify(payload(text, opts)),
  });
  const items = data?.searchResults?.items;
  if (!Array.isArray(items)) throw new Error('ShopGoodwill returned an unexpected response shape');
  return items;
}

function image(item) {
  const raw = item.imageURL || item.imageUrl || '';
  if (!raw) return null;
  if (/^https?:/i.test(raw)) return raw;
  return item.imageServer ? `${item.imageServer}${raw}` : null;
}

async function search(ctx, term, category, limit) {
  const items = await query(ctx, term, { pageSize: limit });
  return items
    .map((i) => ({
      id: `sgw_${i.itemId}`,
      source: 'shopgoodwill',
      category,
      title: i.title || '',
      price: Number(i.currentPrice ?? i.minimumBid) || null,
      isAuction: !(Number(i.buyNowPrice) > 0 && !i.numBids),
      buyNow: Number(i.buyNowPrice) > 0 ? Number(i.buyNowPrice) : null,
      bids: i.numBids ?? null,
      endsAt: i.endTime || null,
      url: `https://shopgoodwill.com/item/${i.itemId}`,
      image: image(i),
      sellerRating: null,
      shipping: null,
      condition: null,
      location: null,
    }))
    .filter((l) => l.title && l.id !== 'sgw_undefined');
}

async function comps(ctx, q) {
  const items = await query(ctx, q, { closed: true, pageSize: 40 });
  return items
    .filter((i) => Number(i.numBids) > 0 && Number(i.currentPrice) > 0)
    .map((i) => ({ title: i.title || '', price: Number(i.currentPrice), kind: 'sold' }));
}

module.exports = { name: 'shopgoodwill', label: 'ShopGoodwill', needsKeys: false, search, comps };
