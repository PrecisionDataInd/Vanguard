'use strict';
// Price-based signals on daily candles. Each returns null or
// {type, score, entry, stop, target, rr, rationale}. They describe a setup;
// they are not predictions and never place a trade.
const { sma, ema, rsi, bollinger, atr, clamp } = require('../core/indicators');

const last = (arr, back = 0) => arr[arr.length - 1 - back];
const pct = (n) => `${(n * 100).toFixed(1)}%`;
const money = (n) => (n >= 100 ? n.toFixed(0) : n.toFixed(2));

function finish(type, score, entry, stop, target, rationale) {
  if (!(stop < entry && target > entry)) return null;
  const rr = (target - entry) / (entry - stop);
  return { type, score: Math.round(clamp(score) * 100) / 100, entry, stop, target, rr: Math.round(rr * 10) / 10, rationale };
}

function momentumBreakout(c) {
  if (c.length < 60) return null;
  const closes = c.map((x) => x.c);
  const prior = c.slice(-21, -1);
  const hi20 = Math.max(...prior.map((x) => x.h));
  const volAvg = prior.reduce((a, x) => a + x.v, 0) / prior.length;
  const e20 = last(ema(closes, 20));
  const e50 = last(ema(closes, 50));
  const r = last(rsi(closes));
  const a = last(atr(c));
  const px = last(closes);
  const volRatio = volAvg ? last(c).v / volAvg : 0;
  if (!(px > hi20 && volRatio >= 1.5 && e20 > e50 && r >= 55 && r <= 80)) return null;
  const score = 0.35 * clamp((volRatio - 1) / 2) + 0.35 * clamp((e20 / e50 - 1) / 0.05) + 0.3 * clamp(1 - Math.abs(r - 65) / 20);
  return finish(
    'momentum_breakout',
    0.35 + 0.65 * score,
    px,
    px - 2 * a,
    px + 4 * a,
    `Closed above its 20-day high on ${volRatio.toFixed(1)}x normal volume, with the 20-day average above the 50-day and RSI at ${r.toFixed(0)}.`
  );
}

function oversoldReversion(c) {
  if (c.length < 40) return null;
  const closes = c.map((x) => x.c);
  const bb = bollinger(closes);
  const r = last(rsi(closes));
  const a = last(atr(c));
  const px = last(closes);
  const prev = last(closes, 1);
  const mid = last(bb.mid);
  if (!(prev < last(bb.lower, 1) && px > prev && r < 40)) return null;
  const stop = Math.min(...c.slice(-3).map((x) => x.l)) - 0.5 * a;
  const gap = (mid - px) / px;
  const out = finish(
    'oversold_reversion',
    0.4 + 0.3 * clamp((40 - r) / 20) + 0.3 * clamp(gap / 0.12),
    px,
    stop,
    mid,
    `Fell below its lower Bollinger band, then closed up; RSI ${r.toFixed(0)}. The 20-day average sits ${pct(gap)} above the current price.`
  );
  return out && out.rr >= 1.2 ? out : null;
}

function uptrendPullback(c) {
  if (c.length < 200) return null;
  const closes = c.map((x) => x.c);
  const s50 = last(sma(closes, 50));
  const s200 = last(sma(closes, 200));
  const r = last(rsi(closes));
  const a = last(atr(c));
  const px = last(closes);
  if (!(px > s200 && px <= s50 * 0.96 && r < 45)) return null;
  const depth = (s50 - px) / s50;
  return finish(
    'uptrend_pullback',
    0.4 + 0.3 * clamp(depth / 0.1) + 0.15 * clamp((45 - r) / 20) + 0.15 * clamp((px / s200 - 1) / 0.1),
    px,
    px - 2 * a,
    s50,
    `Still above its 200-day average but ${pct(depth)} below the 50-day, RSI ${r.toFixed(0)}: a dip inside a longer uptrend.`
  );
}

function qualityDrawdown(c) {
  if (c.length < 200) return null;
  const year = c.slice(-252);
  const hi = Math.max(...year.map((x) => x.h));
  const px = last(c).c;
  const weekAgo = last(c, 5).c;
  const dd = 1 - px / hi;
  if (!(dd >= 0.15 && px >= weekAgo * 0.99)) return null;
  return finish(
    'quality_drawdown',
    0.45 + 0.35 * clamp((dd - 0.15) / 0.2) + 0.2 * clamp((px / weekAgo - 0.99) / 0.05),
    px,
    px * 0.9,
    px + (hi - px) * 0.5,
    `Down ${pct(dd)} from its 52-week high of $${money(hi)} and steady over the past week. Price-only check: confirm the dividend and business are intact before acting.`
  );
}

const SIGNALS = {
  momentum_breakout: momentumBreakout,
  oversold_reversion: oversoldReversion,
  uptrend_pullback: uptrendPullback,
  quality_drawdown: qualityDrawdown,
};

const LABELS = {
  momentum_breakout: 'Momentum breakout',
  oversold_reversion: 'Oversold bounce',
  uptrend_pullback: 'Pullback in uptrend',
  quality_drawdown: 'Quality on sale',
};

module.exports = { SIGNALS, LABELS };
