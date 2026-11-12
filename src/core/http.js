'use strict';
// fetch wrapper: timeout, one retry on 429/5xx, JSON parsing, readable errors.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function request(fetchImpl, url, opts = {}) {
  const { timeoutMs = 20000, retries = 1, ...init } = opts;
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetchImpl(url, { ...init, signal: ctrl.signal });
      clearTimeout(timer);
      if (res.status === 429 || res.status >= 500) {
        lastErr = new Error(`HTTP ${res.status} from ${new URL(url).host}`);
        if (attempt < retries) {
          await sleep(1500 * (attempt + 1));
          continue;
        }
        throw lastErr;
      }
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status} from ${new URL(url).host}${body ? `: ${body.slice(0, 160)}` : ''}`);
      }
      return res;
    } catch (err) {
      clearTimeout(timer);
      lastErr = err.name === 'AbortError' ? new Error(`Timed out reaching ${new URL(url).host}`) : err;
      if (attempt >= retries) throw lastErr;
      await sleep(1000);
    }
  }
  throw lastErr;
}

async function getJson(fetchImpl, url, opts) {
  const res = await request(fetchImpl, url, opts);
  return res.json();
}

module.exports = { request, getJson, sleep };
