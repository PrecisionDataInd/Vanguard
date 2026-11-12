"""Alpaca Markets data layer — market data and account info."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from shared.database import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"

_client = None
_data_client = None

# Rate limiting: 200 req/min for Alpaca
_RATE_LIMIT_KEY = "alpaca:rate_counter"
_RATE_LIMIT_MAX = 190  # leave headroom
_RATE_LIMIT_WINDOW = 60  # seconds


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _check_rate_limit() -> bool:
    """Check if we're within Alpaca rate limits. Returns True if OK."""
    try:
        r = db.get_redis()
        count = r.get(_RATE_LIMIT_KEY)
        if count and int(count) >= _RATE_LIMIT_MAX:
            return False
        pipe = r.pipeline()
        pipe.incr(_RATE_LIMIT_KEY)
        pipe.expire(_RATE_LIMIT_KEY, _RATE_LIMIT_WINDOW)
        pipe.execute()
        return True
    except Exception:
        return True  # allow on Redis failure


def _cache_get(key: str) -> str | None:
    try:
        return db.get_redis().get(key)
    except Exception:
        return None


def _cache_set(key: str, value: str, ttl: int = 300) -> None:
    try:
        db.get_redis().setex(key, ttl, value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------

def _init_clients() -> None:
    global _client, _data_client

    if _data_client is not None:
        return

    cfg = _load_config()
    api_key = cfg["api_keys"].get("alpaca_api_key", "")
    secret_key = cfg["api_keys"].get("alpaca_secret_key", "")

    if not api_key or api_key.startswith("YOUR_"):
        logger.warning("Alpaca API keys not configured")
        return

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        _data_client = StockHistoricalDataClient(api_key, secret_key)
        _client = TradingClient(api_key, secret_key, paper=True)
        logger.info("Alpaca clients initialized")
    except ImportError:
        logger.error("alpaca-py not installed. Run: pip install alpaca-py")
    except Exception as exc:
        logger.error(f"Alpaca client init failed: {exc}")


# ---------------------------------------------------------------------------
# Data functions
# ---------------------------------------------------------------------------

def get_bars(
    symbol: str,
    timeframe: str = "1Day",
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch OHLCV bars for a symbol.

    Args:
        symbol: Stock/ETF symbol (e.g. "AAPL").
        timeframe: One of 1Min, 5Min, 15Min, 1Hour, 1Day.
        limit: Number of bars.

    Returns:
        DataFrame with columns: open, high, low, close, volume, timestamp
    """
    _init_clients()
    if _data_client is None:
        return pd.DataFrame()

    cache_key = f"alpaca:bars:{symbol}:{timeframe}:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return pd.read_json(cached)

    for attempt in range(3):
        if not _check_rate_limit():
            logger.warning("Alpaca rate limit reached, waiting...")
            import time
            time.sleep(2 ** attempt)
            continue

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            tf_map = {
                "1Min": TimeFrame(1, TimeFrameUnit.Minute),
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
                "1Day": TimeFrame(1, TimeFrameUnit.Day),
            }
            tf = tf_map.get(timeframe, TimeFrame(1, TimeFrameUnit.Day))

            end = datetime.now(timezone.utc)
            # Estimate start based on limit and timeframe
            if "Day" in timeframe:
                start = end - timedelta(days=limit * 2)
            elif "Hour" in timeframe:
                start = end - timedelta(hours=limit * 2)
            else:
                start = end - timedelta(minutes=limit * 10)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                limit=limit,
            )

            bars = _data_client.get_stock_bars(request)
            if symbol in bars:
                data = []
                for bar in bars[symbol]:
                    data.append({
                        "timestamp": bar.timestamp,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume),
                    })
                df = pd.DataFrame(data)
                _cache_set(cache_key, df.to_json(), ttl=300)
                return df

            return pd.DataFrame()

        except Exception as exc:
            logger.error(f"get_bars({symbol}) attempt {attempt + 1}/3 failed: {exc}")
            if attempt < 2:
                import time
                time.sleep(2 ** (attempt + 1))

    return pd.DataFrame()


def get_latest_quote(symbol: str) -> dict:
    """Fetch the latest quote for a symbol.

    Returns:
        dict with keys: bid, ask, last
    """
    _init_clients()
    if _data_client is None:
        return {"bid": 0, "ask": 0, "last": 0}

    cache_key = f"alpaca:quote:{symbol}"
    cached = _cache_get(cache_key)
    if cached:
        import json
        return json.loads(cached)

    for attempt in range(3):
        if not _check_rate_limit():
            import time
            time.sleep(2 ** attempt)
            continue

        try:
            from alpaca.data.requests import StockLatestQuoteRequest

            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = _data_client.get_stock_latest_quote(request)

            if symbol in quotes:
                q = quotes[symbol]
                result = {
                    "bid": float(q.bid_price),
                    "ask": float(q.ask_price),
                    "last": float((q.bid_price + q.ask_price) / 2),
                }
                import json
                _cache_set(cache_key, json.dumps(result), ttl=60)
                return result

            return {"bid": 0, "ask": 0, "last": 0}

        except Exception as exc:
            logger.error(f"get_latest_quote({symbol}) attempt {attempt + 1}/3: {exc}")
            if attempt < 2:
                import time
                time.sleep(2 ** (attempt + 1))

    return {"bid": 0, "ask": 0, "last": 0}


def get_snapshot(symbols: list[str]) -> dict[str, dict]:
    """Fetch snapshots for multiple symbols.

    Returns:
        dict mapping symbol -> {price, change, change_pct, volume, high, low, prev_close}
    """
    _init_clients()
    if _data_client is None:
        return {}

    results = {}
    for attempt in range(3):
        if not _check_rate_limit():
            import time
            time.sleep(2 ** attempt)
            continue

        try:
            from alpaca.data.requests import StockSnapshotRequest

            request = StockSnapshotRequest(symbol_or_symbols=symbols)
            snapshots = _data_client.get_stock_snapshot(request)

            for sym, snap in snapshots.items():
                prev_close = float(snap.previous_daily_bar.close) if snap.previous_daily_bar else 0
                current = float(snap.latest_trade.price) if snap.latest_trade else 0
                change = current - prev_close if prev_close else 0
                change_pct = (change / prev_close * 100) if prev_close else 0

                results[sym] = {
                    "price": current,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": int(snap.daily_bar.volume) if snap.daily_bar else 0,
                    "high": float(snap.daily_bar.high) if snap.daily_bar else 0,
                    "low": float(snap.daily_bar.low) if snap.daily_bar else 0,
                    "prev_close": prev_close,
                }

            return results

        except Exception as exc:
            logger.error(f"get_snapshot attempt {attempt + 1}/3: {exc}")
            if attempt < 2:
                import time
                time.sleep(2 ** (attempt + 1))

    return results


def get_account() -> dict:
    """Get paper trading account info.

    Returns:
        dict with keys: cash, portfolio_value, positions
    """
    _init_clients()
    if _client is None:
        return {"cash": 0, "portfolio_value": 0, "positions": []}

    try:
        account = _client.get_account()
        return {
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "positions": [],
        }
    except Exception as exc:
        logger.error(f"get_account failed: {exc}")
        return {"cash": 0, "portfolio_value": 0, "positions": []}


def get_positions() -> list[dict]:
    """Get all paper trading positions from Alpaca.

    Returns:
        List of position dicts.
    """
    _init_clients()
    if _client is None:
        return []

    try:
        positions = _client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]
    except Exception as exc:
        logger.error(f"get_positions failed: {exc}")
        return []


def get_price(symbol: str) -> float:
    """Get the current price for a stock/ETF symbol."""
    quote = get_latest_quote(symbol)
    return quote.get("last", 0)


async def test_connection() -> bool:
    """Test Alpaca API connection."""
    _init_clients()
    if _data_client is None:
        return False
    try:
        snap = get_snapshot(["AAPL"])
        return "AAPL" in snap
    except Exception as exc:
        logger.error(f"Alpaca connection test failed: {exc}")
        return False
