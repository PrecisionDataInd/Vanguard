# Vanguard build plan

Updated 12 November 2026. Replaces the March 2026 "master prompt v2" plan.

## Where the project stood

The repo held the March build: about 4,400 lines of Python for a purchasing
layer and an investor bot, sitting on Postgres, Redis, Docker and a Telegram
bot. It had never run. The review found:

| Finding | Consequence |
|---|---|
| The Phase 1 deal finder (scrapers, scoring, category files) was never committed. The purchasing layer imports from a folder that is not in the repo. | Nothing could start. |
| Valuation relied on eBay's Finding API (`findCompletedItems`). eBay shut that API down on 5 Feb 2025. | No fair values, so every score was meaningless. |
| With no comparables, the old scorer assumed a 20% discount, then fell back to the average price of a whole search term ("tektronix oscilloscope") as the item's value. | Fabricated "86% below market" alerts. |
| Auction current bids were treated as the purchase price. | Every fresh auction looked like a bargain. |
| Keyword matching was by substring ("AP" matched "apple"; "poster" as a reject word killed the "vintage poster" search). | Wrong boosts and wrong rejections. |
| Six of eight scrapers used guessed CSS selectors and were never run against the live sites. | Would return nothing. |
| Half the investor signals needed data the chosen sources do not provide (P/E, payout ratio, dividend streaks, earnings surprises, ETF NAV). | Those signals could not fire honestly. |
| Position sizing used a Kelly formula with invented win rates. | False precision. |
| The checkout flow pasted the full card number and CVV into an AI agent's prompt. | Card details exposed to every page the agent reads. |
| Setup needed Docker Desktop, Postgres, Redis, Node, a Python venv and seven API keys before anything appeared on screen. | Not testable by a non-developer. |
| The repo is public. | Config with real keys must never be committed. |

## What was borrowed from other projects

- **ai-marketplace-monitor** (BoPeng, GitHub): cheap keyword and price filters
  first, AI only on survivors; a 1 to 5 AI rating with a one-line reason;
  support for a local Ollama model; a built-in log view so problems are visible.
- **Vinted/eBay sold-price bot** (dev.to, jdpg23): only sold prices count as
  value; filter parts and accessories out of comparables; subtract selling fees
  and shipping both ways before calling anything a profit; show how many
  comparables back each estimate; "zero deals today" is the normal result.
- **Portfolio trackers such as Ghostfolio**: pool cards with equity history, a
  positions table, a plain trade log.

## Principles (unchanged, now enforced in code)

1. The app finds; the person decides. Nothing is bought or traded by the app.
2. Paper money before real money. No broker connection exists in this build.
3. Never score on a guess. No comparables means "needs your eye", not a number.
4. Category knowledge stays in editable YAML, separate from code.
5. One installer, no services to run, a demo mode that works with no accounts.

## Milestones

### M1: desktop app (done in v0.2.x)

- Electron app, single Windows installer, data in one local file, API keys
  encrypted with Windows credential protection.
- Deal engine: rule prefilter, comparable lookup with model-number matching,
  outlier trimming, sold-versus-asking confidence, profit after fees and
  shipping, maximum bid for auctions, 0-100 score.
- Sources: ShopGoodwill (search plus closed-auction sold prices, no key) and
  eBay Browse API (free keys; active listings and asking prices).
- Investing: Coinbase daily prices (no key), Alpaca daily prices (free key),
  four price-only signals, three paper pools (40/35/25), risk-based sizing with
  a 10% cash floor, stop and target alerts that never sell on their own.
- Inbox for all decisions; optional Telegram ping; optional AI second opinion
  (Ollama or Anthropic) that can reject but never approve.
- Demo mode, 18 automated tests, real-app smoke test.

### M2: prove the live sources (next)

- First run on the Windows PC with demo mode off. Confirm ShopGoodwill search
  and sold-price responses match what the code expects; adjust field names.
- Create the eBay developer keyset; confirm Browse works with a new key.
- Watch one week of results. Tune thresholds, red-flag words and search terms
  from real false positives. Record every "Pursue" and "Skip" as training data.
- Add an in-app "why was this rejected" view for tuning the YAML files.

### M3: more places to look, better values

- HiBid (has a GraphQL API), GovDeals, PublicSurplus and GSA Auctions for heavy
  and lab equipment, each added only after a recorded real response.
- Local pickup radius from a home ZIP for freight-only categories.
- Sold-price history kept locally, so values improve the longer it runs.
- Photo-based condition check through the AI second opinion.
- A fundamentals source for the Steady pool (dividend history and payout
  ratio), which brings back the dividend signals removed in M1.
- Backtest each signal on five years of prices before trusting its score.

### M4: resale tracking

- Log what each bought item sold for; show real profit against the estimate
  per category. This is the number that says whether the system works.

### M5: assisted purchase (only after M2 to M4 show real profit)

- Redesigned from scratch: the app never holds or types card details. If
  pursued, use a card provider's own tokenised checkout, a per-purchase cap,
  and a confirmation in the app for every single order.
- Live trading stays out of scope. Paper results would need a long, honest
  record first, and that would be a separate decision.

## Known gaps in v0.2.1

- The two live deal sources and both price feeds could not be reached from the
  build machine, so they are verified only against documented response shapes.
- The installer is unsigned; Windows SmartScreen will warn on first run.
- The program file itself (`Vanguard.exe` in Explorer) keeps the default
  Electron icon when built on Linux; the window, taskbar, shortcuts and
  installer use the Vanguard crest. A build on Windows fixes the file icon.
- The old Python build is kept in `legacy/` for reference and is not used.
