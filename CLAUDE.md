# Vanguard: notes for Claude Code

- Electron app. All logic is in `src/` and must stay runnable under plain Node
  (`npm test`). `main.js` and `preload.js` are the only Electron-aware files.
- Hard rules: the app never purchases, never places a real trade, never stores
  card details. Every decision goes through the Inbox.
- Never score a listing without at least four matching comparables.
- Category knowledge lives in `knowledge/categories/*.yaml`; keep rules there,
  not in code.
- A new data source needs: a recorded real response, a normaliser, a health
  entry, and a test. Do not add scrapers with guessed selectors.
- This repository is public. Never commit keys, tokens or personal details.
- The owner is not a developer: ship changes as a tested installer, and keep
  wording in the interface plain.
- `legacy/` is reference only.
- Plan and status: `BUILD_PLAN.md`.
