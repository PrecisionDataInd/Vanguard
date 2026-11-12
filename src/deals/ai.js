'use strict';
// Optional second opinion on a deal that already passed the rules.
// Runs on a local Ollama model (free, private) or the Anthropic API.
// The AI can only lower confidence or add a note; it never approves anything.
const { getJson } = require('../core/http');

function prompt(listing, val, cat) {
  return [
    'You review second-hand listings for a reseller. Be skeptical.',
    `Category: ${cat.display_name}`,
    `Title: ${listing.title}`,
    listing.description ? `Description: ${listing.description.slice(0, 600)}` : '',
    `Price: $${listing.price}${listing.isAuction ? ' (current auction bid)' : ''}`,
    `Estimated resale: $${val.fair} from ${val.compCount} ${val.basis} comparables`,
    'Rate 1-5 how likely this is a genuine bargain (5 = great). Consider: is it the full item or a part/accessory,',
    'signs it is broken or fake, and whether the comparables likely match.',
    'Reply with JSON only: {"rating": <1-5>, "reason": "<one sentence>"}',
  ]
    .filter(Boolean)
    .join('\n');
}

function parse(text) {
  const m = String(text).match(/\{[\s\S]*\}/);
  if (!m) throw new Error('AI reply had no JSON');
  const obj = JSON.parse(m[0]);
  const rating = Math.round(Number(obj.rating));
  if (!(rating >= 1 && rating <= 5)) throw new Error('AI rating out of range');
  return { rating, reason: String(obj.reason || '').slice(0, 240) };
}

async function review(ctx, listing, val, cat) {
  const ai = ctx.settings.ai || {};
  const text = prompt(listing, val, cat);
  if (ai.provider === 'ollama') {
    const data = await getJson(ctx.fetch, `${ai.ollamaUrl || 'http://localhost:11434'}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: ai.ollamaModel || 'llama3.1:8b', prompt: text, stream: false, format: 'json' }),
      timeoutMs: 90000,
      retries: 0,
    });
    return parse(data.response);
  }
  if (ai.provider === 'anthropic') {
    if (!ctx.secrets.anthropicKey) throw new Error('Anthropic key not set');
    const data = await getJson(ctx.fetch, 'https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ctx.secrets.anthropicKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: ai.anthropicModel || 'claude-haiku-5-5',
        max_tokens: 200,
        messages: [{ role: 'user', content: text }],
      }),
      timeoutMs: 60000,
    });
    return parse(data.content?.[0]?.text);
  }
  return null;
}

module.exports = { review, parse };
