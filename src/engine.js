'use strict';
// The app's brain, independent of Electron so it can be tested with plain
// Node. main.js supplies the data folder, a secret store and a way to open
// links; everything else lives here.
const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const { createStore } = require('./core/store');
const { getJson } = require('./core/http');
const { scanDeals, activeSources } = require('./deals/scout');
const { scanInvest } = require('./invest/monitor');
const paper = require('./invest/paper');

const SECRET_KEYS = ['ebayClientId', 'ebayClientSecret', 'alpacaKey', 'alpacaSecret', 'telegramToken', 'telegramChatId', 'anthropicKey'];

const DEFAULTS = {
  settings: {
    demoMode: true,
    dealsPaused: false,
    investPaused: false,
    dealIntervalMin: 30,
    alertThreshold: 0.55,
    minProfit: 25,
    targetRoi: 0.5,
    sources: { shopgoodwill: true, ebay: true },
    categories: {},
    telegramEnabled: false,
    ai: { provider: 'off', ollamaUrl: 'http://localhost:11434', ollamaModel: 'llama3.1:8b', anthropicModel: 'claude-haiku-5-5' },
  },
  deals: { items: {}, comps: {}, cursor: {}, health: {}, lastScan: 0 },
  invest: { capital: 10000, pools: {}, alerts: [], trades: [], lastScan: {}, prices: {}, health: null },
  log: [],
};

function loadKnowledge(dir) {
  const categories = {};
  const catDir = path.join(dir, 'categories');
  for (const file of fs.readdirSync(catDir).filter((f) => f.endsWith('.yaml')).sort()) {
    const doc = yaml.load(fs.readFileSync(path.join(catDir, file), 'utf8'));
    if (doc && doc.category) categories[doc.category] = doc;
  }
  const pools = yaml.load(fs.readFileSync(path.join(dir, 'pools.yaml'), 'utf8')).pools;
  return { categories, pools };
}

function createEngine({ dataDir, knowledgeDir, secretStore, fetchImpl, openExternal, onChange, version }) {
  const store = createStore(path.join(dataDir, 'vanguard-data.json'), DEFAULTS);
  const state = store.data;
  const knowledge = loadKnowledge(knowledgeDir);
  const secrets = secretStore.load();
  const busy = { deals: false, invest: false };
  let timer = null;
  let lastInvestTick = 0;
  let notifyBudget = { minute: 0, count: 0 };

  if (!Object.keys(state.invest.pools).length) paper.initPools(state.invest, knowledge.pools, state.invest.capital);

  const emit = () => {
    store.save();
    if (onChange) onChange();
  };

  function log(level, message) {
    state.log.unshift({ at: Date.now(), level, message });
    if (state.log.length > 300) state.log.length = 300;
    emit();
  }

  function notify(text, url) {
    if (!state.settings.telegramEnabled || !secrets.telegramToken || !secrets.telegramChatId) return;
    const minute = Math.floor(Date.now() / 60000);
    if (notifyBudget.minute !== minute) notifyBudget = { minute, count: 0 };
    if (++notifyBudget.count > 8) return; // never flood the phone
    sendTelegram(url ? `${text}\n${url}` : text).catch((err) => log('warn', `Telegram: ${err.message}`));
  }

  function sendTelegram(text) {
    return getJson(fetchImpl, `https://api.telegram.org/bot${secrets.telegramToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: secrets.telegramChatId, text: `Vanguard\n${text}`, disable_web_page_preview: true }),
    });
  }

  const ctx = {
    state,
    knowledge,
    secrets,
    fetch: fetchImpl,
    log,
    emit,
    notify,
    get settings() {
      return state.settings;
    },
  };

  async function guarded(kind, fn) {
    if (busy[kind]) return { skipped: true };
    busy[kind] = true;
    emit();
    try {
      return await fn();
    } catch (err) {
      log('error', `${kind === 'deals' ? 'Deal' : 'Market'} scan failed: ${err.message}`);
      return { error: err.message };
    } finally {
      busy[kind] = false;
      emit();
    }
  }

  function tick() {
    const now = Date.now();
    const s = state.settings;
    if (!s.dealsPaused && now - state.deals.lastScan >= s.dealIntervalMin * 60e3) guarded('deals', () => scanDeals(ctx));
    if (!s.investPaused && now - lastInvestTick >= 15 * 60e3) {
      lastInvestTick = now;
      guarded('invest', () => scanInvest(ctx));
    }
  }

  function dealRec(id) {
    const rec = state.deals.items[id];
    if (!rec) throw new Error('That deal is no longer in the list');
    return rec;
  }

  const api = {
    getState() {
      const items = Object.entries(state.deals.items)
        .filter(([, r]) => r.status !== 'rejected')
        .map(([id, r]) => ({ id, ...r }))
        .sort((a, b) => (b.val.score || 0) - (a.val.score || 0) || b.firstSeen - a.firstSeen)
        .slice(0, 800);
      const counts = {};
      for (const r of Object.values(state.deals.items)) counts[r.status] = (counts[r.status] || 0) + 1;
      const pools = {};
      for (const [id, cfg] of Object.entries(knowledge.pools)) {
        const p = state.invest.pools[id];
        if (!p) continue;
        pools[id] = {
          name: cfg.name,
          horizon: cfg.horizon,
          allocation: cfg.allocation,
          signals: cfg.signals,
          watchCount: (cfg.watch.stocks || []).length + (cfg.watch.crypto || []).length,
          cash: p.cash,
          start: p.start,
          peak: p.peak,
          realized: p.realized,
          equity: Math.round(paper.equity(p) * 100) / 100,
          history: p.history,
          positions: Object.entries(p.positions).map(([symbol, pos]) => ({ symbol, ...pos })),
        };
      }
      return {
        version,
        settings: state.settings,
        secretsSet: Object.fromEntries(SECRET_KEYS.map((k) => [k, !!secrets[k]])),
        scanning: { ...busy },
        categories: Object.values(knowledge.categories).map((c) => ({ id: c.category, name: c.display_name })),
        sourcesActive: activeSources(ctx).map((s) => s.label),
        deals: { lastScan: state.deals.lastScan, health: state.deals.health, counts, items },
        invest: {
          capital: state.invest.capital,
          pools,
          alerts: state.invest.alerts.slice(0, 150),
          trades: state.invest.trades.slice(0, 60),
          health: state.invest.health,
          hasStockData: state.settings.demoMode || !!(secrets.alpacaKey && secrets.alpacaSecret),
        },
        log: state.log.slice(0, 200),
      };
    },

    scanDeals: () => guarded('deals', () => scanDeals(ctx)),
    scanInvest: () => guarded('invest', () => scanInvest(ctx, { force: true })),

    dealAction(id, action, payload = {}) {
      const rec = dealRec(id);
      if (action === 'pursue') rec.status = 'pursuing';
      else if (action === 'skip') rec.status = 'skipped';
      else if (action === 'reopen') rec.status = 'inbox';
      else if (action === 'snooze') {
        rec.status = 'snoozed';
        rec.snoozeUntil = Date.now() + (Number(payload.hours) || 4) * 3600e3;
      } else if (action === 'bought') {
        const paid = Number(payload.paid);
        if (!(paid >= 0)) throw new Error('Enter what you paid');
        rec.status = 'bought';
        rec.paid = paid;
        rec.boughtAt = Date.now();
      } else if (action === 'note') rec.note = String(payload.note || '').slice(0, 500);
      else throw new Error(`Unknown action: ${action}`);
      rec.updatedAt = Date.now();
      emit();
      return true;
    },

    alertAction(id, action, payload = {}) {
      const alert = state.invest.alerts.find((a) => a.id === id);
      if (!alert) throw new Error('That alert is no longer in the list');
      if (action === 'approve') {
        if (alert.kind !== 'signal' || alert.status !== 'pending') throw new Error('This alert can no longer be approved');
        const price = state.invest.prices[alert.symbol]?.c || alert.entry;
        paper.buy(state.invest, alert, Number(payload.usd), price);
        alert.status = 'approved';
        alert.filledUsd = Number(payload.usd);
        alert.filledPrice = price;
        log('info', `Paper buy: $${alert.filledUsd} of ${alert.symbol} at $${price.toFixed(2)} (${knowledge.pools[alert.pool].name})`);
      } else if (action === 'pass') alert.status = 'passed';
      else if (action === 'dismiss') alert.status = 'dismissed';
      else if (action === 'note') alert.note = String(payload.note || '').slice(0, 500);
      else throw new Error(`Unknown action: ${action}`);
      paper.snapshot(state.invest);
      emit();
      return true;
    },

    closePosition(poolId, symbol) {
      const pnl = paper.sell(state.invest, poolId, symbol);
      log('info', `Paper sell: ${symbol} closed, ${pnl >= 0 ? 'gain' : 'loss'} of $${Math.abs(pnl).toFixed(2)}`);
      paper.snapshot(state.invest);
      emit();
      return pnl;
    },

    saveSettings(patch = {}) {
      const s = state.settings;
      const wasDemo = s.demoMode;
      const num = (v, lo, hi, fallback) => (Number.isFinite(Number(v)) ? Math.min(hi, Math.max(lo, Number(v))) : fallback);
      for (const k of ['demoMode', 'dealsPaused', 'investPaused', 'telegramEnabled']) if (k in patch) s[k] = !!patch[k];
      if ('dealIntervalMin' in patch) s.dealIntervalMin = num(patch.dealIntervalMin, 10, 1440, s.dealIntervalMin);
      if ('alertThreshold' in patch) s.alertThreshold = num(patch.alertThreshold, 0.2, 0.95, s.alertThreshold);
      if ('minProfit' in patch) s.minProfit = num(patch.minProfit, 0, 10000, s.minProfit);
      if ('targetRoi' in patch) s.targetRoi = num(patch.targetRoi, 0.05, 5, s.targetRoi);
      if (patch.sources) for (const k of ['shopgoodwill', 'ebay']) if (k in patch.sources) s.sources[k] = !!patch.sources[k];
      if (patch.categories) for (const id of Object.keys(knowledge.categories)) if (id in patch.categories) s.categories[id] = !!patch.categories[id];
      if (patch.ai) {
        if (['off', 'ollama', 'anthropic'].includes(patch.ai.provider)) s.ai.provider = patch.ai.provider;
        for (const k of ['ollamaUrl', 'ollamaModel', 'anthropicModel']) if (typeof patch.ai[k] === 'string' && patch.ai[k].trim()) s.ai[k] = patch.ai[k].trim();
      }
      if (wasDemo !== s.demoMode) {
        // Sample data and real data must never mix.
        state.deals.items = {};
        state.deals.comps = {};
        state.deals.health = {};
        state.deals.lastScan = 0;
        state.invest.prices = {};
        state.invest.health = null;
        paper.initPools(state.invest, knowledge.pools, state.invest.capital);
        lastInvestTick = 0;
        log('info', s.demoMode ? 'Demo mode on: showing sample data' : 'Demo mode off: using live sources. Lists and the paper portfolio were reset.');
      }
      emit();
      return true;
    },

    saveSecrets(patch = {}) {
      for (const k of SECRET_KEYS) {
        if (!(k in patch)) continue;
        const v = String(patch[k] || '').trim();
        if (v) secrets[k] = v;
        else delete secrets[k];
      }
      secretStore.save(secrets);
      emit();
      return true;
    },

    resetPaper(capital) {
      const amount = Number(capital);
      if (!(amount >= 100 && amount <= 100000000)) throw new Error('Enter a starting amount of at least $100');
      paper.initPools(state.invest, knowledge.pools, amount);
      log('info', `Paper portfolio reset to $${amount.toLocaleString()}`);
      return true;
    },

    async testTelegram() {
      if (!secrets.telegramToken || !secrets.telegramChatId) throw new Error('Save a bot token and chat ID first');
      await sendTelegram('Test message: notifications are working.');
      return true;
    },

    openExternal(url) {
      if (!/^https?:\/\//i.test(String(url))) throw new Error('Blocked: not a web link');
      openExternal(url);
      return true;
    },
  };

  return {
    api,
    start() {
      setTimeout(tick, 2500);
      timer = setInterval(tick, 60e3);
    },
    stop() {
      if (timer) clearInterval(timer);
      store.flush();
    },
    _ctx: ctx,
  };
}

module.exports = { createEngine, loadKnowledge, SECRET_KEYS };
