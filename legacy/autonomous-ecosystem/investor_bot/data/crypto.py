"""Crypto market data via Coinbase Advanced Trade (primary) and Binance (fallback)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml
from loguru import logger

from shared.database import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"

_coinbase_client = None

# Rate limiting: Coinbase 10 req/sec
_CB_RATE_KEY = "coinbase:rate_counter"
_CB_RATE_MAX = 8  # headroom
_CB_RATE_WINDOW = 1

BINANCE_BASE_URL = "https://api.binance.com/api/v3"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _check_coinbase_rate() -> bool:
    try:
        r = db.get_redis()
        count = r.get(_CB_RATE_KEY)
        if count and int(count) >= _CB_RATE_MAX:
            return False
        pipe = r.pipeline()
        pipe.incr(_CB_RATE_KEY)
        pipe.expire(_CB_RATE_KEY, _CB_RATE_WINDOW)
        pipe.execute()
        return True
    except Exception:
        return True


def _cache_get(key: str) -> str | None:
    try:
        return db.get_redis().get(key)
    except Exception:
        return None


def _cache_set(key: str, value: str, ttl: int = 60) -> None:
    try:
        db.get_redis().setex(key, ttl, value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Coinbase client
# ---------------------------------------------------------------------------

def _init_coinbase() -> None:
    global _coinbase_client
    if _coinbase_client is not None:
        return

    cfg = _load_config()
    api_key = cfg["api_keys"].get("coinbase_api_key", "")
    api_secret = cfg["api_keys"].get("coinbase_api_secret", "")

    if not api_key or api_key.startswith("YOUR_"):
        logger.warning("Coinbase API keys not configured, using Binance fallback")
        return

    try:
        from coinbase.rest import RESTClient

        _coinbase_client = RESTClient(api_key=api_key, api_secret=api_secret)
        logger.info("Coinbase Advanced Trade client initialized")
    except ImportError:
        logger.warning("coinbase-advanced-py not installed, using Binance fallback")
    except Exception as exc:
        logger.error(f"Coinbase client init failed: {exc}")


# ---------------------------------------------------------------------------
# Binance fallback (public API, no auth needed)
# ---------------------------------------------------------------------------

def _binance_symbol(symbol: str) -> str:
    """Convert 'BTC-USD' to 'BTCUSDT' for Binance."""
    return symbol.replace("-USD", "USDT").replace("-", "")


def _binance_get_price(symbol: str) -> float:
    """Get price from Binance public API."""
    bn_sym = _binance_symbol(symbol)
    try:
        resp = requests.get(
            f"{BINANCE_BASE_URL}/ticker/price",
            params={"symbol": bn_sym},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as exc:
        logger.error(f"Binance price fetch failed for {symbol}: {exc}")
        return 0.0


def _binance_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Get OHLCV from Binance public API."""
    bn_sym = _binance_symbol(symbol)
    try:
        resp = requests.get(
            f"{BINANCE_BASE_URL}/klines",
            params={"symbol": bn_sym, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception as exc:
        logger.error(f"Binance klines fetch failed for {symbol}: {exc}")
        return pd.DataFrame()


def _binance_get_24h(symbol: str) -> dict:
    """Get 24h stats from Binance."""
    bn_sym = _binance_symbol(symbol)
    try:
        resp = requests.get(
            f"{BINANCE_BASE_URL}/ticker/24hr",
            params={"symbol": bn_sym},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "open": float(d["openPrice"]),
            "high": float(d["highPrice"]),
            "low": float(d["lowPrice"]),
            "close": float(d["lastPrice"]),
            "volume": float(d["volume"]),
            "change_pct": float(d["priceChangePercent"]),
        }
    except Exception as exc:
        logger.error(f"Binance 24h stats failed for {symbol}: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

GRANULARITY_MAP = {
    "ONE_MINUTE": "1m",
    "FIVE_MINUTE": "5m",
    "FIFTEEN_MINUTE": "15m",
    "ONE_HOUR": "1h",
    "ONE_DAY": "1d",
}


def get_price(symbol: str) -> float:
    """Get current price for a crypto pair (e.g. 'BTC-USD').

    Tries Coinbase first, falls back to Binance.
    """
    cache_key = f"crypto:price:{symbol}"
    cached = _cache_get(cache_key)
    if cached:
        return float(cached)

    # Try Coinbase
    _init_coinbase()
    if _coinbase_client and _check_coinbase_rate():
        try:
            product = _coinbase_client.get_product(symbol)
            price = float(product.price) if hasattr(product, "price") else 0
            if price > 0:
                _cache_set(cache_key, str(price), ttl=60)
                return price
        except Exception as exc:
            logger.warning(f"Coinbase price fetch failed for {symbol}: {exc}")

    # Fallback to Binance
    price = _binance_get_price(symbol)
    if price > 0:
        _cache_set(cache_key, str(price), ttl=60)
    return price


def get_ohlcv(
    symbol: str,
    granularity: str = "ONE_DAY",
    limit: int = 100,
) -> pd.DataFrame:
    """Get OHLCV candles for a crypto pair.

    Args:
        symbol: e.g. "BTC-USD"
        granularity: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, ONE_HOUR, ONE_DAY
        limit: number of candles

    Returns:
        DataFrame with timestamp, open, high, low, close, volume
    """
    cache_key = f"crypto:ohlcv:{symbol}:{granularity}:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return pd.read_json(cached)

    # Try Coinbase
    _init_coinbase()
    if _coinbase_client and _check_coinbase_rate():
        try:
            from datetime import datetime, timedelta, timezone

            end = datetime.now(timezone.utc)
            # Estimate start
            interval_minutes = {
                "ONE_MINUTE": 1, "FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15,
                "ONE_HOUR": 60, "ONE_DAY": 1440,
            }
            mins = interval_minutes.get(granularity, 1440)
            start = end - timedelta(minutes=mins * limit * 1.5)

            candles = _coinbase_client.get_candles(
                product_id=symbol,
                start=str(int(start.timestamp())),
                end=str(int(end.timestamp())),
                granularity=granularity,
            )

            if candles and hasattr(candles, "candles"):
                data = []
                for c in candles.candles[:limit]:
                    data.append({
                        "timestamp": pd.to_datetime(int(c.start), unit="s"),
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    })
                df = pd.DataFrame(data)
                if not df.empty:
                    df = df.sort_values("timestamp").reset_index(drop=True)
                    _cache_set(cache_key, df.to_json(), ttl=60)
                    return df
        except Exception as exc:
            logger.warning(f"Coinbase OHLCV failed for {symbol}: {exc}")

    # Fallback to Binance
    bn_interval = GRANULARITY_MAP.get(granularity, "1d")
    df = _binance_get_klines(symbol, bn_interval, limit)
    if not df.empty:
        _cache_set(cache_key, df.to_json(), ttl=60)
    return df


def get_24h_stats(symbol: str) -> dict:
    """Get 24-hour trading statistics.

    Returns:
        dict with keys: open, high, low, close, volume, change_pct
    """
    cache_key = f"crypto:24h:{symbol}"
    cached = _cache_get(cache_key)
    if cached:
        return json.loads(cached)

    # Try Coinbase
    _init_coinbase()
    if _coinbase_client and _check_coinbase_rate():
        try:
            product = _coinbase_client.get_product(symbol)
            if product:
                stats = {
                    "open": float(getattr(product, "open_24h", 0) or 0),
                    "high": float(getattr(product, "high_24h", 0) or 0),
                    "low": float(getattr(product, "low_24h", 0) or 0),
                    "close": float(getattr(product, "price", 0) or 0),
                    "volume": float(getattr(product, "volume_24h", 0) or 0),
                    "change_pct": float(getattr(product, "price_percentage_change_24h", 0) or 0),
                }
                _cache_set(cache_key, json.dumps(stats), ttl=60)
                return stats
        except Exception as exc:
            logger.warning(f"Coinbase 24h stats failed for {symbol}: {exc}")

    # Fallback to Binance
    stats = _binance_get_24h(symbol)
    if stats:
        _cache_set(cache_key, json.dumps(stats), ttl=60)
    return stats


async def test_connection() -> bool:
    """Test crypto data connection (Coinbase or Binance)."""
    try:
        price = get_price("BTC-USD")
        return price > 0
    except Exception as exc:
        logger.error(f"Crypto connection test failed: {exc}")
        return False
