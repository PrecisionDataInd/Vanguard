# Autonomous Asset Acquisition & Investment Ecosystem

A production-grade system consisting of two integrated bots running on a Windows 11 PC:

1. **Deal Scout Phase 2** — Autonomous purchasing layer that extends an existing deal-finding agent with AgentCard virtual cards and Browserbase browser automation for checkout.
2. **Investor Bot** — Three-pool investment scanner that monitors stocks, ETFs, and crypto for high-upside opportunities using Alpaca Markets and Coinbase/Binance APIs.

**Every action requires human Telegram approval.** No trade is ever executed automatically. No purchase happens without your explicit OK.

---

## Prerequisites

- **Python 3.11+** — [python.org](https://python.org)
- **Docker Desktop** — [docker.com](https://www.docker.com/products/docker-desktop/)
- **Node.js** (optional, for AgentCard CLI) — [nodejs.org](https://nodejs.org)

## Installation (Windows)

```batch
cd autonomous-ecosystem
scripts\setup.bat
```

The setup script will:
1. Verify Python 3.11+ and Docker Desktop
2. Start PostgreSQL 16 and Redis 7 containers
3. Create a Python virtual environment
4. Install all dependencies
5. Apply the database schema
6. Run connection tests

## API Key Configuration

Edit `config/config.yaml` and fill in your API keys:

| Service | Key | Where to Get |
|---------|-----|-------------|
| **Telegram** | `telegram_bot_token`, `telegram_chat_id` | [@BotFather](https://t.me/BotFather) on Telegram |
| **Alpaca** | `alpaca_api_key`, `alpaca_secret_key` | [alpaca.markets](https://alpaca.markets/) — use Data API keys (free) |
| **Coinbase** | `coinbase_api_key`, `coinbase_api_secret` | [Coinbase Advanced Trade](https://www.coinbase.com/settings/api) |
| **AgentCard** | `agentcard_mcp_url`, `agentcard_api_key` | AgentCard dashboard |
| **Browserbase** | `browserbase_api_key` | [browserbase.com](https://browserbase.com) |
| **Anthropic** | `anthropic_api_key` | [console.anthropic.com](https://console.anthropic.com) |

## Pool Configuration

### pools.yaml

Three investment pools with different strategies:

| Pool | Allocation | Scan Interval | Risk | Time Horizon |
|------|-----------|--------------|------|-------------|
| **Aggressive** | 40% | 15 min | High | Weeks to months |
| **Balanced** | 35% | 2 hours | Medium | Months to 1-2 years |
| **Steady** | 25% | Daily | Low | 5-10 years |

Edit `config/investment/pools.yaml` to adjust allocations, min scores, and signal types.

### watchlist.yaml

Edit `config/investment/watchlist.yaml` to add or remove symbols from the screener universe.

## Running

```batch
:: Start everything
scripts\run_all.bat

:: Or run individually
scripts\run_deal_scout_phase2.bat
scripts\run_investor.bat
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Show both systems status and last scan time |
| `/portfolio` | Show all three pools: value, cash %, positions, P&L |
| `/deals` | Last 10 scored deals with scores |
| `/alerts` | Pending approvals awaiting response |
| `/pause` | Pause both systems |
| `/resume` | Resume both systems |
| `/pause_deals` | Pause deal scout only |
| `/pause_investor` | Pause investor bot only |

## Approval Flow

### Deal Alerts

When Deal Scout finds a deal above threshold:

1. You receive a Telegram message with price, estimated value, discount %, and score
2. Tap **BUY IT** to approve or **SKIP** to reject
3. On approval: virtual card issued → browser opens listing → checkout attempted → confirmation sent
4. On skip: deal is logged and never re-surfaced

### Trade Alerts

When Investor Bot finds an opportunity:

1. You receive a Telegram alert with symbol, pool, signal type, score, and rationale
2. Tap **EXECUTE** to approve or **PASS** to reject
3. Tap **MORE INFO** for extended data (RSI, volume, pool health)
4. Tap **NOTE** to attach a note to the alert
5. On approval: paper trade is recorded at current market price
6. All approvals expire after 4 hours if not responded to

## Paper Trading

**All trades are paper-simulated.** The system:

- Tracks positions in a local PostgreSQL database
- Fetches real market prices for valuation
- Simulates fills at current market price
- Tracks P&L per position and per pool
- Takes periodic pool value snapshots
- Alerts you if any position drops 15%+ from entry

No real money is ever at risk. No brokerage orders are placed.

## Alert Types

### Deal Alert Example
```
DEAL — APPROVAL REQUIRED
Vintage Camera Lot (35mm film cameras)
Source: eBay
Asking: $125.00
Est. Value: ~$340.00 (63% below market)
Score: 78%
[BUY IT] [SKIP] [VIEW] [REMIND ME IN 2H]
```

### Trade Alert Example
```
TRADE ALERT — AGGRESSIVE POOL
BUY NVDA (stock)
Estimated size: ~$600.00
Signal: momentum_breakout
Score: 82% | Conviction: HIGH
Rationale: RSI crossed 55 with 2.3x average volume...
Current price: $145.20
[EXECUTE] [PASS] [MORE INFO] [NOTE]
```

## Safety Caps

| Cap | Value | Protection |
|-----|-------|-----------|
| Max single purchase | $500 | Prevents overspending on any one deal |
| Card buffer | +15% | Covers shipping/tax |
| Max card amount | $575 | Hard cap ($500 + 15%) |
| Daily deal spend | $1,000 | Limits total daily purchases |
| Paper trading only | Enforced | No live brokerage calls in this build |
| Max alerts per scan | 3 | Prevents Telegram spam |
| Alert cooldown | 6 hours | No duplicate alerts for same symbol |
| Pool drawdown halt | 20% | Auto-pauses scanning if pool drops 20% |
| Approval expiry | 4 hours | Stale approvals auto-expire |

## Troubleshooting

### Docker containers won't start
- Ensure Docker Desktop is running
- Run `docker-compose up -d` manually and check logs with `docker-compose logs`

### No alerts appearing
- Check `/status` in Telegram to verify systems are running
- Verify API keys are configured (not `YOUR_*` placeholders)
- Run `scripts\test_connections.bat` to verify all connections
- Check `logs/` directory for error details

### Telegram bot not responding
- Verify `telegram_bot_token` and `telegram_chat_id` in config
- Ensure only one instance of the bot is running (Telegram allows only one polling connection)
- Try sending `/start` to your bot

### AgentCard/Browserbase unavailable
- This is expected if those services aren't configured yet
- Deal Scout will continue alerting — purchasing will just be disabled
- Check the Telegram startup message for service availability

### Database connection errors
- Run `docker ps` to verify PostgreSQL container is running
- Check that port 5432 isn't used by another PostgreSQL instance
- Try `docker-compose down && docker-compose up -d` to restart

### Redis connection errors
- Verify Redis container is running: `docker ps`
- Check port 6379 isn't in use
- The system will function without Redis (caching disabled) but performance may suffer
