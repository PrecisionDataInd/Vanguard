'use strict';
/* Renderer: draws the engine's state. All listing text comes from the open web,
   so everything is added with textContent, never as HTML. */
(() => {
  const api = window.vanguard;
  let S = null;
  let view = 'inbox';
  let dealFilter = 'inbox';
  let catFilter = '';
  let pendingRender = false;

  // ---- helpers ---------------------------------------------------------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k in el && k !== 'list') el[k] = v;
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
    return el;
  }
  const $ = (id) => document.getElementById(id);
  const usd = (n, d = 0) => (n == null ? '--' : `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`);
  const px = (n) => usd(n, n < 100 ? 2 : 0);
  const pctText = (n) => (n == null ? '--' : `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`);
  const sign = (n) => (n > 0 ? 'good' : n < 0 ? 'bad' : '');
  const ago = (t) => {
    if (!t) return 'never';
    const m = Math.round((Date.now() - t) / 60000);
    return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  };
  const until = (iso) => {
    const m = Math.round((Date.parse(iso) - Date.now()) / 60000);
    if (!(m > 0)) return 'ended';
    return m < 60 ? `${m} min left` : m < 2880 ? `${Math.round(m / 60)} h left` : `${Math.round(m / 1440)} d left`;
  };
  const catName = (id) => (S.categories.find((c) => c.id === id) || {}).name || id;
  const SOURCE = { ebay: 'eBay', shopgoodwill: 'ShopGoodwill', demo: 'DEMO' };

  let toastTimer;
  function toast(msg, isErr) {
    const t = $('toast');
    t.textContent = msg;
    t.className = `show${isErr ? ' err' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.className = ''), isErr ? 6000 : 2600);
  }
  async function call(method, ...args) {
    const res = await api.call(method, ...args);
    if (!res.ok) {
      toast(res.error, true);
      throw new Error(res.error);
    }
    return res.value;
  }
  async function act(method, args, okMsg) {
    try {
      await call(method, ...args);
      if (okMsg) toast(okMsg);
      await refresh(true);
    } catch { /* toast already shown */ }
  }

  // ---- deal card -------------------------------------------------------
  function dealCard(d) {
    const l = d.listing;
    const v = d.val;
    const thumb = l.image
      ? h('img', { class: 'thumb', src: l.image, alt: '', loading: 'lazy', referrerPolicy: 'no-referrer' })
      : h('div', { class: 'thumb' }, (catName(l.category) || '?')[0]);
    const timing = l.isAuction ? `Auction${l.bids != null ? `, ${l.bids} bid${l.bids === 1 ? '' : 's'}` : ''}${l.endsAt ? `, ${until(l.endsAt)}` : ''}` : 'Buy it now';
    const nums = [];
    nums.push(h('div', { class: 'num' }, h('span', {}, l.isAuction ? 'Current bid' : 'Price'), h('b', {}, px(l.price))));
    if (v.fair != null) {
      nums.push(h('div', { class: 'num' }, h('span', {}, 'Est. resale'), h('b', {}, px(v.fair)), h('small', {}, `${v.compCount} ${v.basis === 'sold' ? 'sold' : 'asking'}, ${v.confidence} confidence`)));
      nums.push(h('div', { class: 'num' }, h('span', {}, 'Profit after fees'), h('b', { class: sign(v.netProfit) }, usd(v.netProfit)), h('small', {}, `${pctText(v.roi)} return`)));
      if (l.isAuction) nums.push(h('div', { class: 'num' }, h('span', {}, 'Stay under'), h('b', {}, px(v.maxPrice))));
    }
    const chips = [
      ...(v.boosters || []).map((b) => h('span', { class: 'chip plus' }, b)),
      ...((v.flags && v.flags.major) || []).map((f) => h('span', { class: 'chip minus' }, f)),
      ...((v.flags && v.flags.minor) || []).map((f) => h('span', { class: 'chip' }, f)),
    ];
    const buttons = [h('button', { class: 'btn', onclick: () => call('openExternal', l.url).catch(() => {}) }, 'Open listing')];
    const A = (label, action, payload, cls, msg) =>
      h('button', { class: `btn ${cls || ''}`, onclick: () => act('dealAction', [d.id, action, payload], msg) }, label);
    if (d.status === 'inbox') buttons.push(A('Pursue', 'pursue', {}, 'primary', 'Moved to Pursuing'), A('Snooze 4 h', 'snooze', { hours: 4 }), A('Skip', 'skip', {}, 'quiet'));
    else if (d.status === 'pursuing') {
      const paid = h('input', { type: 'number', min: 0, step: 1, value: l.price ?? '', 'aria-label': 'Amount paid' });
      buttons.push(h('button', { class: 'btn primary', onclick: () => act('dealAction', [d.id, 'bought', { paid: paid.value }], 'Logged as bought') }, 'I bought it for'), paid, A('Drop', 'skip', {}, 'quiet'));
    } else if (d.status === 'bought') buttons.push(h('span', { class: 'meta' }, `Bought for ${px(d.paid)} ${ago(d.boughtAt)}`));
    else buttons.push(A('Move to inbox', 'reopen', {}, '', 'Moved to inbox'));

    return h('article', { class: 'card' },
      thumb,
      h('div', {},
        v.score ? h('span', { class: 'score', title: 'Deal score, 0-100' }, Math.round(v.score * 100)) : null,
        h('h3', {}, l.title),
        h('div', { class: 'meta' }, `${SOURCE[l.source] || l.source} | ${catName(l.category)} | ${timing} | first seen ${ago(d.firstSeen)}`),
        h('div', { class: 'nums' }, nums),
        chips.length ? h('div', { class: 'chips' }, chips) : null,
        h('p', { class: 'why' }, v.reason || '', d.ai ? ` AI second opinion: ${d.ai.rating}/5. ${d.ai.reason}` : ''),
        h('div', { class: 'row' }, buttons)
      )
    );
  }

  // ---- trade alert card ------------------------------------------------
  function alertCard(a) {
    const pool = S.invest.pools[a.pool] || {};
    if (a.kind === 'position') {
      return h('article', { class: 'card trade' }, h('div', {},
        h('h3', {}, `${a.symbol}: ${a.label}`),
        h('div', { class: 'meta' }, `${pool.name} | ${ago(a.createdAt)}`),
        h('p', { class: 'why' }, a.rationale),
        h('div', { class: 'row' },
          h('button', { class: 'btn primary', onclick: async () => { try { await call('closePosition', a.pool, a.symbol); await call('alertAction', a.id, 'dismiss'); toast('Position closed (paper)'); refresh(true); } catch {} } }, 'Close position (paper)'),
          h('button', { class: 'btn', onclick: () => act('alertAction', [a.id, 'dismiss']) }, 'Keep holding'))
      ));
    }
    const amount = h('input', { type: 'number', min: 0, step: 10, value: a.suggestedUsd || 0, 'aria-label': 'Dollar amount' });
    return h('article', { class: 'card trade' }, h('div', {},
      h('span', { class: 'score', title: 'Signal score, 0-100' }, Math.round(a.score * 100)),
      h('h3', {}, `${a.symbol}: ${a.label}`),
      h('div', { class: 'meta' }, `${pool.name} | ${a.asset} | ${a.conviction} conviction | ${ago(a.createdAt)}`),
      h('div', { class: 'nums' },
        h('div', { class: 'num' }, h('span', {}, 'Price'), h('b', {}, px(a.entry))),
        h('div', { class: 'num' }, h('span', {}, 'Stop'), h('b', {}, px(a.stop)), h('small', {}, pctText(a.stop / a.entry - 1))),
        h('div', { class: 'num' }, h('span', {}, 'Target'), h('b', {}, px(a.target)), h('small', {}, pctText(a.target / a.entry - 1))),
        h('div', { class: 'num' }, h('span', {}, 'Reward : risk'), h('b', {}, `${a.rr} : 1`))),
      h('p', { class: 'why' }, a.rationale),
      h('div', { class: 'row' },
        h('span', { class: 'meta' }, 'Paper amount $'), amount,
        h('button', { class: 'btn primary', onclick: () => act('alertAction', [a.id, 'approve', { usd: amount.value }], `Paper buy recorded: ${a.symbol}`) }, 'Approve paper buy'),
        h('button', { class: 'btn quiet', onclick: () => act('alertAction', [a.id, 'pass']) }, 'Pass'),
        a.suggestedUsd ? null : h('span', { class: 'meta warn' }, 'This pool has no spare cash for a new position.'))
    ));
  }

  // ---- views -----------------------------------------------------------
  const inboxDeals = () => S.deals.items.filter((d) => d.status === 'inbox');
  const pendingAlerts = () => S.invest.alerts.filter((a) => a.status === 'pending');

  function demoNote() {
    return S.settings.demoMode
      ? h('div', { class: 'note' }, h('b', {}, 'Demo mode. '), 'Everything shown is sample data so you can try the buttons. Turn it off in Settings to scan real listings and prices.')
      : null;
  }
  const empty = (title, text) => h('div', { class: 'empty' }, h('h3', {}, title), h('p', {}, text));

  const VIEWS = {
    inbox: {
      title: 'Inbox',
      sub: () => 'Finds waiting for your decision. Nothing is bought or traded without you.',
      draw() {
        const deals = inboxDeals();
        const alerts = pendingAlerts();
        const out = [demoNote()];
        if (!deals.length && !alerts.length) out.push(empty('Nothing needs you right now', `Last deal scan ${ago(S.deals.lastScan)}. Most scans find nothing worth your time, and that is the point.`));
        if (alerts.length) out.push(h('h2', { class: 'section-h' }, `Market signals (${alerts.length})`), alerts.map(alertCard));
        if (deals.length) out.push(h('h2', { class: 'section-h' }, `Deals (${deals.length})`), deals.map(dealCard));
        return out;
      },
    },
    deals: {
      title: 'Deals',
      sub: () => `Sources: ${S.sourcesActive.join(', ') || 'none switched on'} | last scan ${ago(S.deals.lastScan)}`,
      draw() {
        const tabs = [['inbox', 'Inbox'], ['pursuing', 'Pursuing'], ['unpriced', 'Needs your eye'], ['scored', 'Scored, below bar'], ['snoozed', 'Snoozed'], ['bought', 'Bought'], ['skipped', 'Skipped']];
        const list = S.deals.items.filter((d) => d.status === dealFilter && (!catFilter || d.listing.category === catFilter));
        const bar = h('div', { class: 'filters' },
          tabs.map(([id, label]) => h('button', { class: id === dealFilter ? 'on' : '', onclick: () => { dealFilter = id; render(true); } }, `${label} ${S.deals.counts[id] || 0}`)),
          h('select', { 'aria-label': 'Category', onchange: (e) => { catFilter = e.target.value; render(true); } },
            h('option', { value: '' }, 'All categories'),
            S.categories.map((c) => h('option', { value: c.id, selected: c.id === catFilter }, c.name))));
        const out = [demoNote(), bar];
        if (dealFilter === 'unpriced') out.push(h('div', { class: 'note' }, 'These passed the rules but could not be priced automatically: mixed lots, or too few comparable sales. They are never scored on a guess.'));
        if (dealFilter === 'bought' && list.length) {
          const paid = list.reduce((a, d) => a + (d.paid || 0), 0);
          const est = list.reduce((a, d) => a + (d.val.fair || 0), 0);
          out.push(h('div', { class: 'note' }, `${list.length} bought for ${usd(paid)} in total, estimated resale ${usd(est)} before fees.`));
        }
        out.push(list.length ? list.slice(0, 120).map(dealCard) : empty('Nothing here', (S.deals.counts.rejected || 0) ? `${S.deals.counts.rejected} listings were rejected by the category rules.` : 'Run a scan to fill this list.'));
        return out;
      },
    },
    invest: {
      title: 'Investing',
      sub: () => `Paper money: ${usd(Object.values(S.invest.pools).reduce((a, p) => a + p.equity, 0))} across three pools`,
      draw() {
        const out = [demoNote()];
        if (!S.invest.hasStockData) out.push(h('div', { class: 'note' }, h('b', {}, 'Crypto only for now. '), 'Stock and ETF signals need a free Alpaca data key. Add it in Settings; crypto prices need no key.'));
        if (S.invest.health && !S.invest.health.ok) out.push(h('div', { class: 'note' }, h('b', {}, 'Price data problem. '), S.invest.health.errors.join(' | ')));
        out.push(h('div', { class: 'pools' }, Object.entries(S.invest.pools).map(([id, p]) => poolCard(id, p))));
        const positions = Object.entries(S.invest.pools).flatMap(([id, p]) => p.positions.map((pos) => ({ id, pool: p.name, ...pos })));
        out.push(h('h2', { class: 'section-h' }, 'Open paper positions'));
        out.push(positions.length ? table(['Symbol', 'Pool', 'Value', 'Gain / loss', 'Avg cost', 'Last', 'Stop', 'Target', ''],
          positions.map((p) => {
            const value = p.qty * p.last;
            const pnl = value - p.qty * p.avgCost;
            return [p.symbol, p.pool, n(usd(value)), n(`${usd(pnl)} (${pctText(p.last / p.avgCost - 1)})`, sign(pnl)), n(px(p.avgCost)), n(px(p.last)), n(px(p.stop)), n(px(p.target)),
              h('button', { class: 'btn danger', onclick: () => act('closePosition', [p.id, p.symbol], `${p.symbol} closed (paper)`) }, 'Close')];
          })) : h('p', { class: 'meta' }, 'None yet. Approve a signal from the Inbox to open one.'));
        const hist = S.invest.alerts.filter((a) => a.kind === 'signal').slice(0, 25);
        out.push(h('h2', { class: 'section-h' }, 'Recent signals'));
        out.push(hist.length ? table(['When', 'Pool', 'Symbol', 'Signal', 'Score', 'Outcome'],
          hist.map((a) => [ago(a.createdAt), (S.invest.pools[a.pool] || {}).name, a.symbol, a.label, n(Math.round(a.score * 100)), a.status])) : h('p', { class: 'meta' }, 'No signals yet.'));
        if (S.invest.trades.length) {
          out.push(h('h2', { class: 'section-h' }, 'Paper trade log'));
          out.push(table(['When', 'Side', 'Symbol', 'Amount', 'Price', 'Result'],
            S.invest.trades.slice(0, 25).map((t) => [ago(t.at), t.side, t.symbol, n(usd(t.usd)), n(px(t.price)), t.pnl == null ? '' : n(usd(t.pnl, 2), sign(t.pnl))])));
        }
        out.push(h('p', { class: 'fine' }, 'Paper money only: no broker is connected and no real order is ever placed. Signals describe price patterns on daily data. They are not predictions and not financial advice; this app is not a licensed adviser.'));
        return out;
      },
    },
    activity: {
      title: 'Activity',
      sub: () => 'What the scanners did, and whether each source is answering.',
      draw() {
        const health = Object.entries(S.deals.health).map(([name, x]) =>
          h('div', {}, h('span', { class: `dot ${x.ok ? 'ok' : 'err'}` }), `${SOURCE[name] || name}: ${x.ok ? 'answering' : x.error} (${ago(x.at)})`));
        if (S.invest.health) health.push(h('div', {}, h('span', { class: `dot ${S.invest.health.ok ? 'ok' : 'err'}` }), `Price data: ${S.invest.health.ok ? 'answering' : S.invest.health.errors[0]} (${ago(S.invest.health.at)})`));
        return [
          h('h2', { class: 'section-h' }, 'Sources'),
          health.length ? h('div', { class: 'form' }, health) : h('p', { class: 'meta' }, 'No scans have run yet.'),
          h('h2', { class: 'section-h' }, 'Log'),
          h('div', { class: 'log' }, S.log.map((e) => h('div', {}, h('time', {}, new Date(e.at).toLocaleString()), h('span', { class: e.level === 'error' ? 'bad' : e.level === 'warn' ? 'warn' : '' }, e.message)))),
        ];
      },
    },
    settings: { title: 'Settings', sub: () => `Version ${S.version}`, draw: drawSettings },
  };

  const n = (text, cls) => ({ n: true, text, cls });
  function table(heads, rows) {
    return h('div', { class: 'tablewrap' }, h('table', {},
      h('thead', {}, h('tr', {}, heads.map((x) => h('th', {}, x)))),
      h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => (c && c.n ? h('td', { class: `n ${c.cls || ''}` }, c.text) : h('td', {}, c ?? ''))))))));
  }

  function poolCard(id, p) {
    const change = p.equity / p.start - 1;
    const invested = p.equity - p.cash;
    return h('div', { class: 'pool' },
      h('h3', {}, p.name),
      h('div', { class: 'meta' }, `${Math.round(p.allocation * 100)}% of capital | ${p.horizon} | watching ${p.watchCount}`),
      h('div', { class: 'big' }, usd(p.equity)),
      h('div', { class: sign(change) || 'meta' }, `${pctText(change)} since start`),
      spark(p.history),
      h('div', { class: 'kv' }, 'Cash', h('b', {}, usd(p.cash))),
      h('div', { class: 'kv' }, 'Invested', h('b', {}, usd(invested))),
      h('div', { class: 'kv' }, 'Closed-trade result', h('b', { class: sign(p.realized) }, usd(p.realized, 2))),
      h('div', { class: 'kv' }, 'Below peak', h('b', {}, pctText(p.peak ? p.equity / p.peak - 1 : 0))));
  }

  function spark(history) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 200 44');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    const vals = history.map((x) => x.e);
    if (vals.length < 2) vals.unshift(vals[0] ?? 0);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * 200).toFixed(1)},${(hi === lo ? 22 : 40 - ((v - lo) / (hi - lo)) * 36).toFixed(1)}`);
    const line = document.createElementNS(ns, 'polyline');
    line.setAttribute('points', pts.join(' '));
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', '#d9a441');
    line.setAttribute('stroke-width', '1.5');
    line.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.append(line);
    return svg;
  }

  // ---- settings --------------------------------------------------------
  function drawSettings() {
    const s = S.settings;
    const field = (label, input) => h('div', { class: 'field' }, h('label', {}, label), input);
    const check = (label, checked, onchange) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) }), label);
    const save = (patch, msg) => act('saveSettings', [patch], msg || 'Saved');
    const secret = (key) => h('input', { type: 'password', autocomplete: 'off', 'data-secret': key, placeholder: S.secretsSet[key] ? 'Saved. Type to replace' : 'Not set' });
    const saveSecrets = (root, msg) => {
      const patch = {};
      root.querySelectorAll('[data-secret]').forEach((el) => { if (el.value) patch[el.dataset.secret] = el.value; });
      if (!Object.keys(patch).length) return toast('Nothing new to save');
      act('saveSecrets', [patch], msg || 'Keys saved, encrypted on this computer');
    };
    const numIn = (value, step) => h('input', { type: 'number', value, step });

    const interval = numIn(s.dealIntervalMin, 5);
    const threshold = numIn(Math.round(s.alertThreshold * 100), 1);
    const minProfit = numIn(s.minProfit, 5);
    const roi = numIn(Math.round(s.targetRoi * 100), 5);
    const capital = numIn(S.invest.capital, 500);

    const ebay = h('div', { class: 'form' },
      h('h3', {}, 'Deal sources'),
      h('p', {}, 'ShopGoodwill needs no account and supplies real sold prices. eBay needs a free developer keyset (developer.ebay.com, Production keys) and adds asking prices only, because eBay no longer offers sold prices to new developers.'),
      check('ShopGoodwill', s.sources.shopgoodwill, (v) => save({ sources: { shopgoodwill: v } })),
      check('eBay (needs the two keys below)', s.sources.ebay, (v) => save({ sources: { ebay: v } })),
      field('eBay App ID (Client ID)', secret('ebayClientId')),
      field('eBay Cert ID (Client Secret)', secret('ebayClientSecret')));
    ebay.append(h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => saveSecrets(ebay) }, 'Save eBay keys')));

    const markets = h('div', { class: 'form' },
      h('h3', {}, 'Market data'),
      h('p', {}, 'Crypto prices come from Coinbase with no key. Stocks and ETFs need a free Alpaca account (alpaca.markets); the key is used to read prices only.'),
      field('Alpaca API key', secret('alpacaKey')),
      field('Alpaca secret key', secret('alpacaSecret')),
      check('Pause market scanning', s.investPaused, (v) => save({ investPaused: v })));
    markets.append(h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => saveSecrets(markets) }, 'Save Alpaca keys')),
      h('div', { class: 'field' }, h('label', {}, 'Paper starting capital $'), capital),
      h('div', { class: 'row' }, h('button', { class: 'btn danger', onclick: () => { if (confirm('Reset the paper portfolio? All paper positions and history are cleared.')) act('resetPaper', [capital.value], 'Paper portfolio reset'); } }, 'Reset paper portfolio')));

    const tg = h('div', { class: 'form' },
      h('h3', {}, 'Phone notifications (optional)'),
      h('p', {}, 'Sends a short Telegram message when something lands in the Inbox. Decisions are still made here in the app. Create a bot with @BotFather for the token, and get your chat ID from @userinfobot.'),
      check('Send Telegram notifications', s.telegramEnabled, (v) => save({ telegramEnabled: v })),
      field('Bot token', secret('telegramToken')),
      field('Chat ID', secret('telegramChatId')));
    tg.append(h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => saveSecrets(tg) }, 'Save'), h('button', { class: 'btn', onclick: () => act('testTelegram', [], 'Test message sent') }, 'Send test message')));

    const provider = h('select', {}, [['off', 'Off'], ['ollama', 'Ollama (local, free)'], ['anthropic', 'Anthropic API']].map(([v, t]) => h('option', { value: v, selected: s.ai.provider === v }, t)));
    const ollamaModel = h('input', { type: 'text', value: s.ai.ollamaModel });
    const anthModel = h('input', { type: 'text', value: s.ai.anthropicModel });
    const aiForm = h('div', { class: 'form' },
      h('h3', {}, 'AI second opinion (optional)'),
      h('p', {}, 'After a deal passes the rules, an AI model reads the listing and can send it back if it looks like a part, a fake, or a bad match. It can only reject, never approve.'),
      field('Provider', provider), field('Ollama model', ollamaModel), field('Anthropic model', anthModel), field('Anthropic API key', secret('anthropicKey')));
    aiForm.append(h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: async () => {
      const key = aiForm.querySelector('[data-secret]');
      try {
        if (key.value) await call('saveSecrets', { anthropicKey: key.value });
        await call('saveSettings', { ai: { provider: provider.value, ollamaModel: ollamaModel.value, anthropicModel: anthModel.value } });
        toast('Saved');
        refresh(true);
      } catch {}
    } }, 'Save')));

    return [
      h('div', { class: 'form' },
        h('h3', {}, 'Mode'),
        h('p', {}, 'Demo mode fills the app with sample listings and made-up prices. Switching it clears the deal lists and resets the paper portfolio so sample and real data never mix.'),
        check('Demo mode (sample data)', s.demoMode, (v) => {
          if (confirm(v ? 'Switch to demo data? Current lists and the paper portfolio are cleared.' : 'Switch to live data? Demo lists and the paper portfolio are cleared.')) save({ demoMode: v }, v ? 'Demo mode on' : 'Live mode on');
          else render(true);
        })),
      h('div', { class: 'form' },
        h('h3', {}, 'Deal scanning'),
        h('p', {}, 'A deal reaches the Inbox only if it clears the score and still makes the minimum profit after selling fees and shipping both ways.'),
        field('Scan every (minutes)', interval),
        field('Minimum score (0-100)', threshold),
        field('Minimum profit after fees $', minProfit),
        field('Target return for max bid %', roi),
        check('Pause deal scanning', s.dealsPaused, (v) => save({ dealsPaused: v })),
        h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => save({ dealIntervalMin: interval.value, alertThreshold: threshold.value / 100, minProfit: minProfit.value, targetRoi: roi.value / 100 }) }, 'Save'))),
      ebay,
      h('div', { class: 'form' },
        h('h3', {}, 'Categories'),
        h('p', {}, 'Each category has a plain-text knowledge file: search terms, brands, and the words that mark a listing as junk. Edit them in Notepad and restart the app.'),
        h('div', { class: 'cols' }, S.categories.map((c) => check(c.name, s.categories[c.id] !== false, (v) => save({ categories: { [c.id]: v } })))),
        h('div', { class: 'row' }, h('button', { class: 'btn', onclick: () => api.openKnowledge() }, 'Open knowledge folder'))),
      markets, tg, aiForm,
    ];
  }

  // ---- shell -----------------------------------------------------------
  function render(force) {
    const active = document.activeElement;
    if (!force && active && $('view').contains(active) && /INPUT|SELECT|TEXTAREA/.test(active.tagName)) {
      pendingRender = true;
      return;
    }
    pendingRender = false;
    const inboxCount = inboxDeals().length + pendingAlerts().length;
    $('nav').replaceChildren(...[['inbox', 'Inbox', inboxCount], ['deals', 'Deals'], ['invest', 'Investing'], ['activity', 'Activity'], ['settings', 'Settings']].map(([id, label, count]) =>
      h('button', { class: id === view ? 'on' : '', onclick: () => { view = id; render(true); $('view').scrollTop = 0; } }, label, count ? h('span', { class: 'count' }, count) : null)));
    const v = VIEWS[view];
    $('title').textContent = v.title;
    $('subtitle').textContent = v.sub();
    $('actions').replaceChildren(
      h('button', { class: 'btn', disabled: S.scanning.deals, onclick: () => { call('scanDeals').then(() => refresh(true)).catch(() => {}); } }, S.scanning.deals ? 'Scanning deals...' : 'Scan deals now'),
      h('button', { class: 'btn', disabled: S.scanning.invest, onclick: () => { call('scanInvest').then(() => refresh(true)).catch(() => {}); } }, S.scanning.invest ? 'Scanning markets...' : 'Scan markets now'));
    const top = $('view').scrollTop;
    $('view').replaceChildren(...v.draw().flat(Infinity).filter(Boolean));
    $('view').scrollTop = top;
    $('mode').replaceChildren(h('span', { class: `badge ${S.settings.demoMode ? 'demo' : 'live'}` }, S.settings.demoMode ? 'DEMO DATA' : 'LIVE DATA'));
    const busy = S.scanning.deals || S.scanning.invest;
    $('status').replaceChildren(h('span', { class: busy ? 'pulse' : '' }, busy ? 'Scanning...' : S.settings.dealsPaused && S.settings.investPaused ? 'Paused' : `Deals scanned ${ago(S.deals.lastScan)}`));
  }

  async function refresh(force) {
    const res = await api.call('getState');
    if (!res.ok) return toast(res.error, true);
    S = res.value;
    render(force);
  }

  let debounce;
  api.onChanged(() => {
    clearTimeout(debounce);
    debounce = setTimeout(() => refresh(false), 200);
  });
  document.addEventListener('focusout', () => setTimeout(() => pendingRender && render(false), 150));
  setInterval(() => S && render(false), 60000);
  refresh(true);
})();
