'use strict';
// Plain-Node tests: no framework, no network. Run with: npm test
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ind = require('../src/core/indicators');
const text = require('../src/deals/text');
const { evaluate, fairValue } = require('../src/deals/valuation');
const { SIGNALS } = require('../src/invest/signals');
const { demoDaily } = require('../src/invest/data');
const paper = require('../src/invest/paper');
const ai = require('../src/deals/ai');
const { createEngine, loadKnowledge } = require('../src/engine');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const near = (a, b, tol = 1e-6) => assert.ok(Math.abs(a - b) <= tol, `${a} is not within ${tol} of ${b}`);
const knowledge = loadKnowledge(path.join(__dirname, '..', 'knowledge'));
const settings = { alertThreshold: 0.55, minProfit: 25, targetRoi: 0.5 };

// ---- indicators --------------------------------------------------------
test('sma, ema and median', () => {
  assert.deepStrictEqual(ind.sma([1, 2, 3, 4, 5], 3), [null, null, 2, 3, 4]);
  near(ind.ema([1, 2, 3, 4, 5], 3)[4], 4);
  assert.strictEqual(ind.median([5, 1, 3]), 3);
  assert.strictEqual(ind.median([4, 1, 3, 2]), 2.5);
});
test('rsi is 100 on a straight rise and low on a straight fall', () => {
  const up = Array.from({ length: 30 }, (_, i) => 100 + i);
  const down = Array.from({ length: 30 }, (_, i) => 100 - i);
  assert.strictEqual(ind.rsi(up).pop(), 100);
  near(ind.rsi(down).pop(), 0);
});
test('outlier trimming drops the wild comparable', () => {
  const out = ind.trimOutliers([100, 105, 98, 102, 101, 900]);
  assert.ok(!out.includes(900) && out.length === 5);
});

// ---- text matching -----------------------------------------------------
test('phrases match whole words only', () => {
  assert.ok(text.hasPhrase('Signed AP lithograph', 'ap'));
  assert.ok(!text.hasPhrase('Apple laptop', 'ap'));
  assert.ok(!text.hasPhrase('Sealed unopened box', 'opened'));
  assert.ok(text.hasPhrase('sold AS-IS no returns', 'as-is'));
});
test('sameItem requires the model number', () => {
  const t = 'Tektronix TDS2024B 200MHz Oscilloscope';
  assert.ok(text.sameItem(t, 'TEKTRONIX TDS2024B four channel 200mhz digital oscilloscope'));
  assert.ok(!text.sameItem(t, 'Tektronix TDS1002 60MHz Oscilloscope'));
  assert.ok(!text.sameItem(t, 'Tektronix probe for TDS series'));
});
test('lots are recognised', () => {
  assert.ok(text.isLot('Lot of 12 vintage watches'));
  assert.ok(!text.isLot('Pilot watch automatic'));
});

// ---- valuation ---------------------------------------------------------
const scope = { id: 'x', source: 'ebay', category: 'test_equipment', title: 'Tektronix TDS2024B 200MHz Oscilloscope powers on', price: 185, shipping: 28, isAuction: true, sellerRating: 99 };
const soldComps = [520, 480, 560, 495, 610, 450, 530, 505, 575].map((p) => ({ title: 'Tektronix TDS2024B 200MHz Oscilloscope', price: p, kind: 'sold' }));
test('a real bargain reaches the inbox with honest economics', () => {
  const v = evaluate(scope, knowledge.categories.test_equipment, soldComps, settings);
  assert.strictEqual(v.status, 'inbox');
  assert.strictEqual(v.fair, 520);
  assert.strictEqual(v.confidence, 'high');
  // resale 520, minus 13% fees and $28 outbound, minus $185 + $28 inbound
  near(v.netProfit, 520 * 0.87 - 28 - 213, 0.01);
  assert.ok(v.maxPrice > 185 && v.maxPrice < 300);
});
test('parts and junk comparables are ignored', () => {
  const junk = [{ title: 'Tektronix TDS2024B oscilloscope for parts', price: 40, kind: 'sold' }];
  const fv = fairValue(scope, knowledge.categories.test_equipment, [...soldComps.slice(0, 4), ...junk]);
  assert.strictEqual(fv.compCount, 4);
  assert.ok(fv.fair > 400);
});
test('too few comparables means unpriced, never a guess', () => {
  const v = evaluate(scope, knowledge.categories.test_equipment, soldComps.slice(0, 2), settings);
  assert.strictEqual(v.status, 'unpriced');
  assert.strictEqual(v.score, 0);
});
test('asking-price comparables get a haircut and low confidence', () => {
  const asking = soldComps.map((c) => ({ ...c, kind: 'asking' }));
  const fv = fairValue(scope, knowledge.categories.test_equipment, asking);
  assert.strictEqual(fv.confidence, 'low');
  near(fv.fair, 520 * 0.8, 0.01);
});
test('critical red flags reject outright', () => {
  const v = evaluate({ ...scope, title: 'Rolex Submariner replica', category: 'vintage_watches', price: 80 }, knowledge.categories.vintage_watches, [], settings);
  assert.strictEqual(v.status, 'rejected');
});
test('a fairly priced item is scored but stays out of the inbox', () => {
  const drill = { ...scope, title: 'Milwaukee M18 Fuel Hammer Drill 2804-20', category: 'power_tools', price: 115, shipping: 12 };
  const comps = [120, 110, 125, 118, 105].map((p) => ({ title: drill.title, price: p, kind: 'sold' }));
  const v = evaluate(drill, knowledge.categories.power_tools, comps, settings);
  assert.strictEqual(v.status, 'scored');
  assert.ok(v.netProfit < 0);
});
test('mixed lots go to manual review', () => {
  const v = evaluate({ ...scope, title: 'Lot of signed lithographs', category: 'fine_art' }, knowledge.categories.fine_art, [], settings);
  assert.strictEqual(v.status, 'unpriced');
});

// ---- signals -----------------------------------------------------------
test('each demo shape triggers its intended signal', () => {
  const expectHit = (sym, type) => {
    const s = SIGNALS[type](demoDaily(sym));
    assert.ok(s, `${type} did not fire on ${sym}`);
    assert.ok(s.stop < s.entry && s.target > s.entry && s.score > 0 && s.score <= 1);
  };
  expectHit('NVDA', 'momentum_breakout');
  expectHit('SOL-USD', 'oversold_reversion');
  expectHit('SPY', 'uptrend_pullback');
  expectHit('JNJ', 'quality_drawdown');
});
test('a flat series triggers nothing', () => {
  const flat = Array.from({ length: 300 }, (_, i) => ({ t: i, o: 100, h: 100.5, l: 99.5, c: 100, v: 1000 }));
  for (const fn of Object.values(SIGNALS)) assert.strictEqual(fn(flat), null);
});

// ---- paper trading -----------------------------------------------------
test('paper buys respect the cash reserve; sells book the result', () => {
  const invest = { prices: {} };
  paper.initPools(invest, knowledge.pools, 10000);
  const pool = invest.pools.aggressive;
  assert.strictEqual(pool.cash, 4000);
  const alert = { pool: 'aggressive', symbol: 'TEST', stop: 90, target: 120, type: 'momentum_breakout' };
  const size = paper.suggestSize(knowledge.pools.aggressive, pool, 100, 90, 'TEST');
  assert.strictEqual(size, 600); // 1.5% risk / 10% stop distance = 15% cap
  assert.throws(() => paper.buy(invest, alert, 3900, 100), /available/);
  paper.buy(invest, alert, 600, 100);
  assert.strictEqual(pool.cash, 3400);
  pool.positions.TEST.last = 110;
  near(paper.equity(pool), 4060, 0.01);
  near(paper.sell(invest, 'aggressive', 'TEST'), 60, 0.01);
  assert.strictEqual(pool.cash, 4060);
  assert.strictEqual(invest.trades.length, 2);
});

test('AI replies are parsed strictly', () => {
  assert.deepStrictEqual(ai.parse('Sure: {"rating": 2, "reason": "Looks like a part"}'), { rating: 2, reason: 'Looks like a part' });
  assert.throws(() => ai.parse('{"rating": 9}'));
});

// ---- whole engine in demo mode -----------------------------------------
test('engine: demo scan fills the inbox, approvals update the paper book, state survives restart', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vanguard-test-'));
  const saved = {};
  const make = () =>
    createEngine({
      dataDir: dir,
      knowledgeDir: path.join(__dirname, '..', 'knowledge'),
      secretStore: { load: () => ({ ...saved }), save: (o) => Object.assign(saved, o) },
      fetchImpl: () => Promise.reject(new Error('network must not be used in demo mode')),
      openExternal: () => {},
      version: 'test',
    });
  let eng = make();
  await eng.api.scanDeals();
  await eng.api.scanInvest();
  let st = eng.api.getState();
  const inbox = st.deals.items.filter((d) => d.status === 'inbox');
  assert.ok(inbox.length >= 4, `expected several inbox deals, got ${inbox.length}`);
  assert.ok(st.deals.counts.rejected >= 1 && st.deals.counts.unpriced >= 2 && st.deals.counts.scored >= 1);
  assert.ok(!st.deals.items.some((d) => 'demoSold' in d.listing));
  const pending = st.invest.alerts.filter((a) => a.status === 'pending');
  assert.ok(pending.length >= 3, `expected signals, got ${pending.length}`);

  eng.api.dealAction(inbox[0].id, 'pursue');
  eng.api.dealAction(inbox[0].id, 'bought', { paid: 200 });
  const sig = pending.find((a) => a.suggestedUsd > 0);
  eng.api.alertAction(sig.id, 'approve', { usd: sig.suggestedUsd });
  assert.throws(() => eng.api.alertAction(sig.id, 'approve', { usd: 10 }));
  assert.throws(() => eng.api.openExternal('file:///etc/passwd'));
  eng.api.saveSecrets({ alpacaKey: ' abc ' });
  assert.strictEqual(saved.alpacaKey, 'abc');
  eng.stop();

  eng = make(); // restart from disk
  st = eng.api.getState();
  assert.strictEqual(st.deals.items.find((d) => d.id === inbox[0].id).status, 'bought');
  assert.strictEqual(st.invest.pools[sig.pool].positions.length, 1);
  assert.ok(st.secretsSet.alpacaKey && !JSON.stringify(st).includes('abc"'));
  // A second scan must not duplicate anything.
  const before = st.deals.items.length;
  await eng.api.scanDeals();
  assert.strictEqual(eng.api.getState().deals.items.length, before);
  // Leaving demo mode wipes sample data.
  eng.api.saveSettings({ demoMode: false });
  st = eng.api.getState();
  assert.strictEqual(st.deals.items.length, 0);
  assert.strictEqual(st.invest.pools.aggressive.positions.length, 0);
  eng.stop();
  fs.rmSync(dir, { recursive: true, force: true });
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`  ok    ${name}`);
    } catch (err) {
      failed++;
      console.log(`  FAIL  ${name}\n        ${err.message}`);
    }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
