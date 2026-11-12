'use strict';
// Small JSON file store. Atomic writes, debounced saves, corrupt-file recovery.
const fs = require('fs');
const path = require('path');

function merge(defaults, saved) {
  if (Array.isArray(defaults) || typeof defaults !== 'object' || defaults === null) {
    return saved === undefined ? defaults : saved;
  }
  const out = { ...(saved && typeof saved === 'object' ? saved : {}) };
  for (const k of Object.keys(defaults)) out[k] = merge(defaults[k], out[k]);
  return out;
}

function createStore(file, defaults) {
  let saved = {};
  if (fs.existsSync(file)) {
    try {
      saved = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch (err) {
      // Keep the unreadable file so nothing is silently lost, then start clean.
      fs.copyFileSync(file, `${file}.corrupt-${Date.now()}`);
      saved = {};
    }
  }
  const data = merge(defaults, saved);
  let timer = null;

  function flush() {
    if (timer) clearTimeout(timer);
    timer = null;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data));
    fs.renameSync(tmp, file);
  }

  return {
    data,
    save() {
      if (!timer) timer = setTimeout(flush, 400);
    },
    flush,
  };
}

module.exports = { createStore, merge };
