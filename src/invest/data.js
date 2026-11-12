'use strict';
// Daily price candles: Coinbase (crypto, no key), Alpaca (stocks/ETFs, free
// key), and a built-in generator for demo mode. Candle = {t, o, h, l, c, v}.
const { getJson } = require('../core/http');

const isCrypto = (symbol) => symbol.includes('-');

async function coinbaseDaily(ctx, symbol) {
  const rows = await getJson(ctx.fetch, `https://api.exchange.coinbase.com/products/${symbol}/candles?granularity=86400`, {
    headers: { 'User-Agent': 'Vanguard/1.0', Accept: 'application/json' },
  });
  if (!Array.isArray(rows)) throw new Error('Coinbase returned an unexpected response');
  return rows
    .map(([t, l, h, o, c, v]) => ({ t: t * 1000, o, h, l, c, v }))
    .sort((a, b) => a.t - b.t);
}

async function alpacaDaily(ctx, symbols) {
  const { alpacaKey, alpacaSecret } = ctx.secrets;
  if (!alpacaKey || !alpacaSecret) throw new Error('Alpaca keys not set');
  const start = new Date(Date.now() - 420 * 86400e3).toISOString().slice(0, 10);
  const out = {};
  let page = null;
  for (let guard = 0; guard < 20; guard++) {
    const params = new URLSearchParams({
      symbols: symbols.join(','),
      timeframe: '1Day',
      start,
      limit: '10000',
      adjustment: 'all',
      feed: 'iex',
    });
    if (page) params.set('page_token', page);
    const data = await getJson(ctx.fetch, `https://data.alpaca.markets/v2/stocks/bars?${params}`, {
      headers: { 'APCA-API-KEY-ID': alpacaKey, 'APCA-API-SECRET-KEY': alpacaSecret },
    });
    for (const [sym, bars] of Object.entries(data.bars || {})) {
      (out[sym] = out[sym] || []).push(
        ...bars.map((b) => ({ t: Date.parse(b.t), o: b.o, h: b.h, l: b.l, c: b.c, v: b.v }))
      );
    }
    page = data.next_page_token;
    if (!page) break;
  }
  return out;
}

// ---- Demo data --------------------------------------------------------
function rng(seed) {
  let s = [...seed].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7) || 1;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

const DEMO_SHAPES = { NVDA: 'breakout', 'SOL-USD': 'oversold', SPY: 'pullback', JNJ: 'drawdown', 'ETH-USD': 'oversold', SCHD: 'pullback' };
const DEMO_BASE = { 'BTC-USD': 64000, 'ETH-USD': 3100, 'SOL-USD': 150, NVDA: 120, SPY: 540, JNJ: 160 };

function demoDaily(symbol) {
  const rand = rng(symbol);
  const shape = DEMO_SHAPES[symbol] || 'flat';
  const n = 300;
  let price = DEMO_BASE[symbol] || 40 + rand() * 160;
  const closes = [];
  for (let i = 0; i < n; i++) {
    let drift = 0.0003;
    const left = n - 1 - i;
    if (shape === 'breakout') drift = left > 25 ? 0.0012 : left > 0 ? -0.0004 : 0.034;
    if (shape === 'pullback') drift = left > 9 ? 0.0011 : left > 0 ? -0.0085 : 0.002;
    if (shape === 'drawdown') drift = left > 45 ? 0.0006 : left > 7 ? -0.0058 : 0.0015;
    if (shape === 'oversold') drift = left > 6 ? 0.0002 : left > 0 ? -0.024 : 0.012;
    const noise = left === 0 ? 0.002 : shape === 'flat' ? 0.012 : 0.016;
    price *= 1 + drift + (rand() - 0.5) * noise;
    closes.push(price);
  }
  const day = 86400e3;
  const end = Math.floor(Date.now() / day) * day;
  return closes.map((c, i) => {
    const o = i ? closes[i - 1] : c;
    const span = Math.abs(c - o) + c * 0.004;
    let v = 1e6 * (0.8 + rand() * 0.4);
    if (shape === 'breakout' && i === n - 1) v *= 2.6;
    return { t: end - (n - 1 - i) * day, o, h: Math.max(o, c) + span * 0.3, l: Math.min(o, c) - span * 0.3, c, v };
  });
}

// Returns { candles: {symbol: [...]}, errors: [string] }.
async function loadCandles(ctx, symbols) {
  const candles = {};
  const errors = [];
  if (ctx.settings.demoMode) {
    for (const s of symbols) candles[s] = demoDaily(s);
    return { candles, errors };
  }
  const crypto = symbols.filter(isCrypto);
  const stocks = symbols.filter((s) => !isCrypto(s));
  for (const s of crypto) {
    try {
      candles[s] = await coinbaseDaily(ctx, s);
    } catch (err) {
      errors.push(`Coinbase ${s}: ${err.message}`);
    }
  }
  if (stocks.length) {
    try {
      Object.assign(candles, await alpacaDaily(ctx, stocks));
    } catch (err) {
      errors.push(`Alpaca: ${err.message}`);
    }
  }
  return { candles, errors };
}

module.exports = { loadCandles, demoDaily, coinbaseDaily, alpacaDaily, isCrypto };
