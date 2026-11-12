# Vanguard

A Windows desktop app that looks for undervalued second-hand assets and
market setups, then waits for you to decide. It never buys or trades anything.

See [BUILD_PLAN.md](BUILD_PLAN.md) for the review of the earlier build and the
road ahead.

## Try it

1. Run `Vanguard-Setup-0.2.1.exe`. Windows will warn that the publisher is
   unknown (the installer is unsigned): choose **More info**, then **Run anyway**.
2. The app opens in **demo mode** with sample listings and made-up prices, so
   every button can be tried with no accounts.
3. To go live, open **Settings**, switch demo mode off, and add keys as you
   get them. ShopGoodwill and crypto prices need no key at all.

| Feature | Needs |
|---|---|
| ShopGoodwill listings and sold prices | nothing |
| Crypto prices (Coinbase) | nothing |
| eBay listings | free keyset from developer.ebay.com (Production) |
| Stock and ETF prices | free account at alpaca.markets |
| Phone pings | a Telegram bot token and your chat ID |
| AI second opinion | Ollama running locally, or an Anthropic API key |

Your data lives in `%APPDATA%\Vanguard`. Keys are encrypted by Windows and are
never written to this repository.

## How a deal is judged

1. Category rules reject junk (parts, replicas, accessories) by whole-word match.
2. Comparable sales are found by brand and model number, parts and lots are
   excluded, and outliers are trimmed. Fewer than four comparables means the
   listing goes to **Needs your eye** with no score.
3. Value is the median sold price. If only asking prices exist they are cut
   20% and marked low confidence.
4. Profit is resale minus selling fees, minus shipping in and out, minus price.
5. Only listings above your score and minimum-profit settings reach the Inbox.
   Auctions also show the highest bid that still earns your target return.

The rules for each category are plain text files in `knowledge/categories/`.

## Investing side

Three paper-money pools (Aggressive 40%, Balanced 35%, Steady 25%) watch the
symbols in `knowledge/pools.yaml` for four daily-price patterns. Approving a
signal records a pretend purchase. No broker is connected. Signals are not
financial advice.

## For developers

```
npm install
npm start          run from source
npm test           18 tests, no network
npm run dist       build the installer (on Windows)
scripts/build-installer.sh   build the installer on Linux (no Wine needed)
```

`src/engine.js` holds all logic and runs under plain Node. `main.js` is a thin
Electron shell. `legacy/` holds the unused March 2026 Python build.
