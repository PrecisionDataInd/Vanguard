'use strict';
// Paper portfolio: three pools of pretend money. Every buy and sell here is a
// bookkeeping entry made after the user approves it. No broker is contacted.

const CASH_RESERVE = 0.1;

function initPools(invest, poolsCfg, capital) {
  invest.capital = capital;
  invest.pools = {};
  for (const [id, cfg] of Object.entries(poolsCfg)) {
    const cash = Math.round(capital * cfg.allocation * 100) / 100;
    invest.pools[id] = { cash, start: cash, peak: cash, positions: {}, realized: 0, history: [] };
  }
  invest.alerts = [];
  invest.trades = [];
  invest.lastScan = {};
}

function equity(pool) {
  let total = pool.cash;
  for (const p of Object.values(pool.positions)) total += p.qty * p.last;
  return total;
}

function suggestSize(cfg, pool, entry, stop, symbol) {
  const eq = equity(pool);
  const riskDollars = eq * (cfg.risk_pct || 0.01);
  const riskPerDollar = (entry - stop) / entry;
  const byRisk = riskPerDollar > 0 ? riskDollars / riskPerDollar : 0;
  const held = pool.positions[symbol] ? pool.positions[symbol].qty * pool.positions[symbol].last : 0;
  const byCap = eq * (cfg.max_position_pct || 0.1) - held;
  const byCash = pool.cash - eq * CASH_RESERVE;
  return Math.max(0, Math.floor(Math.min(byRisk, byCap, byCash)));
}

function buy(invest, alert, usd, price) {
  const pool = invest.pools[alert.pool];
  if (!pool) throw new Error('Unknown pool');
  if (!(usd > 0)) throw new Error('Enter an amount above zero');
  if (!(price > 0)) throw new Error('No current price available');
  const spendable = pool.cash - equity(pool) * CASH_RESERVE;
  if (usd > spendable + 0.01) {
    throw new Error(`Only $${Math.max(0, Math.floor(spendable))} is available (10% of each pool stays in cash)`);
  }
  const qty = usd / price;
  const pos = pool.positions[alert.symbol];
  if (pos) {
    pos.avgCost = (pos.avgCost * pos.qty + usd) / (pos.qty + qty);
    pos.qty += qty;
    pos.last = price;
    pos.stop = alert.stop;
    pos.target = alert.target;
  } else {
    pool.positions[alert.symbol] = {
      qty,
      avgCost: price,
      last: price,
      stop: alert.stop,
      target: alert.target,
      signal: alert.type,
      openedAt: Date.now(),
    };
  }
  pool.cash = Math.round((pool.cash - usd) * 100) / 100;
  invest.trades.unshift({ at: Date.now(), pool: alert.pool, side: 'BUY', symbol: alert.symbol, qty, price, usd, signal: alert.type });
}

function sell(invest, poolId, symbol) {
  const pool = invest.pools[poolId];
  const pos = pool && pool.positions[symbol];
  if (!pos) throw new Error('No such position');
  const usd = pos.qty * pos.last;
  const pnl = usd - pos.qty * pos.avgCost;
  pool.cash = Math.round((pool.cash + usd) * 100) / 100;
  pool.realized = Math.round((pool.realized + pnl) * 100) / 100;
  delete pool.positions[symbol];
  invest.trades.unshift({ at: Date.now(), pool: poolId, side: 'SELL', symbol, qty: pos.qty, price: pos.last, usd, pnl });
  if (invest.trades.length > 500) invest.trades.length = 500;
  return pnl;
}

function snapshot(invest) {
  const day = new Date().toISOString().slice(0, 10);
  for (const pool of Object.values(invest.pools)) {
    const eq = Math.round(equity(pool) * 100) / 100;
    pool.peak = Math.max(pool.peak || eq, eq);
    const h = pool.history;
    if (h.length && h[h.length - 1].d === day) h[h.length - 1].e = eq;
    else h.push({ d: day, e: eq });
    if (h.length > 400) h.shift();
  }
}

module.exports = { initPools, equity, suggestSize, buy, sell, snapshot, CASH_RESERVE };
