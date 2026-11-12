'use strict';
// Title handling: tokenising, whole-word phrase matching, and deciding whether
// two listings describe the same thing.

const STOP = new Set(
  ('a an and the of for with in on to by from new used vintage old rare nice great lot set w ' +
    'free shipping tested working works condition excellent good very clean original genuine ' +
    'authentic oem pre owned preowned estate sale see photos pics read description fast').split(' ')
);

function tokens(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9.+\-/ ]+/g, ' ')
    .split(/[\s/]+/)
    .map((t) => t.replace(/^[.\-+]+|[.\-+]+$/g, ''))
    .filter(Boolean);
}

function keyTokens(text) {
  return tokens(text).filter((t) => !STOP.has(t) && t.length > 1);
}

// Model-number-like tokens: contain a digit and are at least 3 characters.
function modelTokens(text) {
  return keyTokens(text).filter((t) => /\d/.test(t) && t.length >= 3 && !/^\d{4}s?$/.test(t));
}

// Whole-word, case-insensitive phrase match ("ap" must not match "apple").
function hasPhrase(text, phrase) {
  const esc = String(phrase).toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  return new RegExp(`(^|[^a-z0-9])${esc}($|[^a-z0-9])`, 'i').test(String(text || '').toLowerCase());
}

function findPhrases(text, phrases) {
  return (phrases || []).filter((p) => hasPhrase(text, p));
}

function isLot(title) {
  return /\b(lot|lots|bundle|collection|assorted|misc|box of|bulk)\b/i.test(title || '');
}

// Does a comparable's title describe the same item as the listing?
// With model numbers we require every one to appear; otherwise a strong
// overlap of meaningful words.
function sameItem(listingTitle, compTitle) {
  const want = keyTokens(listingTitle);
  if (!want.length) return false;
  const have = new Set(keyTokens(compTitle));
  const models = modelTokens(listingTitle);
  if (models.length) {
    if (!models.every((m) => have.has(m))) return false;
    const rest = want.filter((t) => !models.includes(t));
    const hit = rest.filter((t) => have.has(t)).length;
    return rest.length === 0 || hit / rest.length >= 0.3;
  }
  const hit = want.filter((t) => have.has(t)).length;
  return want.length >= 3 && hit / want.length >= 0.6;
}

// Short search query for finding comparables: brand/model words first.
function compQuery(title, brand) {
  const models = modelTokens(title);
  const words = keyTokens(title).filter((t) => !models.includes(t));
  const parts = [];
  if (brand) parts.push(...tokens(brand));
  for (const t of [...models, ...words]) {
    if (!parts.includes(t)) parts.push(t);
    if (parts.length >= 5) break;
  }
  return parts.join(' ');
}

module.exports = { tokens, keyTokens, modelTokens, hasPhrase, findPhrases, isLot, sameItem, compQuery };
