'use strict';
// One investing scan: load daily candles for every watched symbol, run each
// pool's signals, and raise alerts for the user to approve or pass.
const { loadCandles, isCrypto } = require('./data');
const { SIGNALS, LABELS } = require('./signals');
const paper = require('./paper');

const COOLDOWN_MS = 24 * 3600e3;
const EXPIRE_MS = 24 * 3600e3;
let seq = 0;
const newId = () => `a${Date.now().toString(36)}${(seq++).toString(36)}`;

function poolSymbols(cfg, hasStockData) {
  const w = cfg.watch || {};
  return [...(hasStockData ? w.stocks || [] : []), ...(w.crypto || [])];
}

async function scanInvest(ctx, { force = false } = {}) {
  const invest = ctx.state.invest;
  const pools = ctx.knowledge.pools;
  const now = Date.now();
  const hasStockData = ctx.settings.demoMode || !!(ctx.secrets.alpacaKey && ctx.secrets.alpacaSecret);
  const due = Object.keys(pools).filter(
    (id) => force || now - (invest.lastScan[id] || 0) >= (pools[id].scan_minutes || 60) * 60e3
  );

  // Positions always get fresh prices, even when their pool is not due.
  const held = new Set();
  for (const pool of Object.values(invest.pools)) Object.keys(pool.positions).forEach((s) => held.add(s));
  const symbols = new Set(held);
  for (const id of due) poolSymbols(pools[id], hasStockData).forEach((s) => symbols.add(s));
  if (!symbols.size) return { raised: 0 };

  const { candles, errors } = await loadCandles(ctx, [...symbols]);
  invest.health = { ok: !errors.length, at: now, errors: errors.slice(0, 5), stocks: hasStockData };
  errors.forEach((e) => ctx.log('warn', e));

  for (const [sym, series] of Object.entries(candles)) {
    if (series.length) invest.prices[sym] = { c: series[series.length - 1].c, at: now };
  }

  let raised = 0;
  for (const id of due) {
    const cfg = pools[id];
    const pool = invest.pools[id];
    if (!pool) continue;
    for (const sym of poolSymbols(cfg, hasStockData)) {
      const series = candles[sym];
      if (!series || pool.positions[sym]) continue;
      for (const type of cfg.signals || []) {
        const fn = SIGNALS[type];
        const sig = fn && fn(series);
        if (!sig || sig.score < (cfg.min_score || 0.6)) continue;
        const recent = invest.alerts.find(
          (a) => a.pool === id && a.symbol === sym && a.type === type && now - a.createdAt < COOLDOWN_MS
        );
        if (recent) continue;
        const alert = {
          id: newId(),
          kind: 'signal',
          pool: id,
          symbol: sym,
          asset: isCrypto(sym) ? 'crypto' : 'stock',
          label: LABELS[type],
          ...sig,
          suggestedUsd: paper.suggestSize(cfg, pool, sig.entry, sig.stop, sym),
          conviction: sig.score >= 0.8 ? 'High' : sig.score >= 0.7 ? 'Medium' : 'Low',
          status: 'pending',
          createdAt: now,
        };
        invest.alerts.unshift(alert);
        raised++;
        ctx.notify(`${cfg.name}: ${alert.label} on ${sym} at $${sig.entry.toFixed(2)} (score ${Math.round(sig.score * 100)})`);
      }
    }
    invest.lastScan[id] = now;
  }

  // Mark positions to market and flag stop/target levels. Never sells.
  for (const [id, pool] of Object.entries(invest.pools)) {
    for (const [sym, pos] of Object.entries(pool.positions)) {
      const px = invest.prices[sym]?.c;
      if (!px) continue;
      pos.last = px;
      const hit = px <= pos.stop ? 'stop' : px >= pos.target ? 'target' : null;
      if (hit && pos.flagged !== hit) {
        pos.flagged = hit;
        invest.alerts.unshift({
          id: newId(),
          kind: 'position',
          pool: id,
          symbol: sym,
          label: hit === 'stop' ? 'Stop level reached' : 'Target reached',
          rationale:
            hit === 'stop'
              ? `${sym} is at $${px.toFixed(2)}, at or below the $${pos.stop.toFixed(2)} stop set when you opened it. Review whether to close.`
              : `${sym} is at $${px.toFixed(2)}, at or above the $${pos.target.toFixed(2)} target. Review whether to take the gain.`,
          status: 'pending',
          createdAt: now,
        });
        ctx.notify(`${pools[id]?.name || id}: ${sym} ${hit} level reached at $${px.toFixed(2)}`);
      }
    }
    const eq = paper.equity(pool);
    pool.peak = Math.max(pool.peak || eq, eq);
  }

  for (const a of invest.alerts) {
    if (a.status === 'pending' && a.kind === 'signal' && now - a.createdAt > EXPIRE_MS) a.status = 'expired';
  }
  if (invest.alerts.length > 400) invest.alerts.length = 400;
  paper.snapshot(invest);
  if (due.length) ctx.log('info', `Market scan (${due.join(', ')}): ${symbols.size} symbols checked, ${raised} new signal${raised === 1 ? '' : 's'}`);
  return { raised };
}

module.exports = { scanInvest };
