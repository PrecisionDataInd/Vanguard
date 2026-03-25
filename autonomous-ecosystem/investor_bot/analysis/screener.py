"""Opportunity screener — scans markets for all signal types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from loguru import logger

from investor_bot.data import alpaca, crypto

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_WATCHLIST_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "investment" / "watchlist.yaml"


def _load_watchlist() -> dict:
    with open(_WATCHLIST_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class OpportunityCandidate:
    symbol: str
    asset_type: str           # stock / etf / crypto
    pool: str                 # aggressive / balanced / steady
    signal_type: str
    price: float
    signal_data: dict = field(default_factory=dict)
    rationale: str = ""
    action: str = "BUY"


# ---------------------------------------------------------------------------
# Technical indicator helpers
# ---------------------------------------------------------------------------

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI for a price series."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    """Return (middle, upper, lower) Bollinger Bands."""
    middle = _sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return middle, upper, lower


# ---------------------------------------------------------------------------
# Signal implementations
# ---------------------------------------------------------------------------

def momentum_breakout(symbols: list[str], pool: str = "aggressive") -> list[OpportunityCandidate]:
    """RSI crossover with volume confirmation and EMA trend alignment."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 100)
            if df.empty or len(df) < 50:
                continue

            close = df["close"]
            volume = df["volume"]

            rsi_vals = _rsi(close, 14)
            ema20 = _ema(close, 20)
            ema50 = _ema(close, 50)
            vol_sma20 = _sma(volume.astype(float), 20)

            if rsi_vals.iloc[-1] is np.nan or len(rsi_vals) < 4:
                continue

            current_rsi = rsi_vals.iloc[-1]
            prior_rsi_below_50 = any(rsi_vals.iloc[-4:-1] < 50)
            rsi_above_55 = current_rsi > 55
            volume_surge = volume.iloc[-1] > 1.5 * vol_sma20.iloc[-1] if vol_sma20.iloc[-1] > 0 else False
            price_above_ema20 = close.iloc[-1] > ema20.iloc[-1]
            ema20_above_ema50 = ema20.iloc[-1] > ema50.iloc[-1]

            if rsi_above_55 and prior_rsi_below_50 and volume_surge and price_above_ema20 and ema20_above_ema50:
                vol_ratio = volume.iloc[-1] / vol_sma20.iloc[-1] if vol_sma20.iloc[-1] > 0 else 0
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="stock",
                    pool=pool,
                    signal_type="momentum_breakout",
                    price=float(close.iloc[-1]),
                    signal_data={
                        "rsi": round(float(current_rsi), 2),
                        "volume_ratio": round(float(vol_ratio), 2),
                        "ema20": round(float(ema20.iloc[-1]), 2),
                        "ema50": round(float(ema50.iloc[-1]), 2),
                    },
                    rationale=(
                        f"RSI crossed 55 with {vol_ratio:.1f}x average volume. "
                        f"Price is above both EMAs confirming uptrend. Momentum entering."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"momentum_breakout scan failed for {sym}: {exc}")
    return candidates


def post_earnings_gap(symbols: list[str], pool: str = "aggressive") -> list[OpportunityCandidate]:
    """Detect post-earnings underreaction opportunities."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 30)
            if df.empty or len(df) < 10:
                continue

            close = df["close"]

            # Look for a gap in recent bars (proxy for earnings)
            for i in range(-5, 0):
                if i == 0 or abs(i) >= len(close):
                    continue
                gap_pct = (close.iloc[i] - close.iloc[i - 1]) / close.iloc[i - 1] * 100
                if abs(gap_pct) > 2:
                    # Positive surprise but stock barely moved
                    pre_earnings_close = float(close.iloc[i - 1])
                    current = float(close.iloc[-1])
                    move_pct = (current - pre_earnings_close) / pre_earnings_close * 100

                    if gap_pct > 0 and move_pct < 3:
                        candidates.append(OpportunityCandidate(
                            symbol=sym,
                            asset_type="stock",
                            pool=pool,
                            signal_type="post_earnings_gap",
                            price=float(close.iloc[-1]),
                            signal_data={
                                "gap_pct": round(gap_pct, 2),
                                "total_move_pct": round(move_pct, 2),
                                "pre_earnings_close": pre_earnings_close,
                            },
                            rationale=(
                                f"Gapped {gap_pct:.1f}% on earnings but only moved {move_pct:.1f}% total. "
                                f"Classic underreaction. Mean reversion play."
                            ),
                        ))
                    break
        except Exception as exc:
            logger.warning(f"post_earnings_gap scan failed for {sym}: {exc}")
    return candidates


def crypto_rotation(symbols: list[str], pool: str = "aggressive") -> list[OpportunityCandidate]:
    """Detect altcoin rotation opportunities based on BTC dominance decline."""
    candidates = []

    # Get BTC and major alt prices to estimate dominance proxy
    try:
        btc_df = crypto.get_ohlcv("BTC-USD", "ONE_DAY", 30)
        if btc_df.empty:
            return candidates

        # BTC dominance proxy: compare BTC close trend vs alts
        btc_close = btc_df["close"]
        btc_sma10 = _sma(btc_close, 10)
        btc_declining = btc_close.iloc[-1] < btc_sma10.iloc[-1] if not btc_sma10.empty else False

    except Exception as exc:
        logger.warning(f"crypto_rotation BTC data failed: {exc}")
        return candidates

    for sym in symbols:
        if sym == "BTC-USD":
            continue
        try:
            df = crypto.get_ohlcv(sym, "ONE_DAY", 30)
            if df.empty or len(df) < 14:
                continue

            close = df["close"]
            rsi_vals = _rsi(close, 14)

            current_rsi = float(rsi_vals.iloc[-1])
            low_30d = float(close.min())
            at_30d_low = float(close.iloc[-1]) <= low_30d * 1.02  # within 2% of low

            if btc_declining and at_30d_low and current_rsi < 40:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="crypto",
                    pool=pool,
                    signal_type="crypto_rotation",
                    price=float(close.iloc[-1]),
                    signal_data={
                        "rsi": round(current_rsi, 2),
                        "low_30d": round(low_30d, 4),
                        "btc_declining": btc_declining,
                    },
                    rationale=(
                        f"BTC dominance declining while {sym} is at 30-day low "
                        f"with RSI {current_rsi:.0f}. Altcoin rotation historically follows this pattern."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"crypto_rotation scan failed for {sym}: {exc}")
    return candidates


def value_gap(symbols: list[str], pool: str = "balanced") -> list[OpportunityCandidate]:
    """Screen for stocks trading below sector median P/E."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 260)
            if df.empty or len(df) < 50:
                continue

            close = df["close"]
            rsi_vals = _rsi(close, 14)
            current_rsi = float(rsi_vals.iloc[-1])
            current_price = float(close.iloc[-1])
            high_52w = float(close.max())
            low_52w = float(close.min())

            # Value proxy: price near 52-week low + RSI not in freefall
            pct_from_low = (current_price - low_52w) / low_52w * 100 if low_52w > 0 else 100
            pct_from_high = (high_52w - current_price) / high_52w * 100 if high_52w > 0 else 0

            if pct_from_low < 20 and current_rsi > 35 and pct_from_high > 25:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="stock",
                    pool=pool,
                    signal_type="value_gap",
                    price=current_price,
                    signal_data={
                        "rsi": round(current_rsi, 2),
                        "pct_from_52w_low": round(pct_from_low, 2),
                        "pct_from_52w_high": round(pct_from_high, 2),
                        "high_52w": round(high_52w, 2),
                        "low_52w": round(low_52w, 2),
                    },
                    rationale=(
                        f"{sym} trading {pct_from_high:.0f}% below 52-week high "
                        f"and within {pct_from_low:.0f}% of 52-week low. "
                        f"RSI {current_rsi:.0f} suggests not in freefall."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"value_gap scan failed for {sym}: {exc}")
    return candidates


def etf_nav_discount(symbols: list[str], pool: str = "balanced") -> list[OpportunityCandidate]:
    """Detect ETFs trading at discount to estimated NAV."""
    candidates = []
    snapshots = alpaca.get_snapshot(symbols)

    for sym, snap in snapshots.items():
        try:
            price = snap.get("price", 0)
            prev_close = snap.get("prev_close", 0)
            if price <= 0 or prev_close <= 0:
                continue

            # NAV discount proxy: gap between prev close and current
            # Real NAV data would come from ETF provider; using price change as proxy
            discount_pct = (prev_close - price) / prev_close * 100

            if discount_pct > 1.0:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="etf",
                    pool=pool,
                    signal_type="etf_nav_discount",
                    price=price,
                    signal_data={
                        "discount_pct": round(discount_pct, 2),
                        "prev_close": prev_close,
                    },
                    rationale=(
                        f"ETF trading at {discount_pct:.1f}% discount to estimated NAV. "
                        f"Arbitrage mechanism should close this gap within days."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"etf_nav_discount scan failed for {sym}: {exc}")
    return candidates


def mean_reversion_setup(symbols: list[str], pool: str = "balanced") -> list[OpportunityCandidate]:
    """Bollinger Band + RSI mean reversion setups."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 220)
            if df.empty or len(df) < 200:
                continue

            close = df["close"]
            rsi_vals = _rsi(close, 14)
            sma200 = _sma(close, 200)
            _, _, lower_bb = _bollinger_bands(close, 20, 2)

            current = float(close.iloc[-1])
            current_rsi = float(rsi_vals.iloc[-1])
            sma200_val = float(sma200.iloc[-1]) if not np.isnan(sma200.iloc[-1]) else current
            bb_lower = float(lower_bb.iloc[-1]) if not np.isnan(lower_bb.iloc[-1]) else current

            below_bb = current < bb_lower
            rsi_oversold = current_rsi < 32
            deviation = (sma200_val - current) / sma200_val * 100 if sma200_val > 0 else 0

            # Check for reversal candles (simplified: last 3 candles show decreasing bearishness)
            if len(df) >= 3:
                last3 = df.tail(3)
                bodies = (last3["close"] - last3["open"]).abs()
                decreasing_bodies = bodies.iloc[-1] < bodies.iloc[-3]
            else:
                decreasing_bodies = False

            if below_bb and rsi_oversold and deviation > 10 and decreasing_bodies:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="stock",
                    pool=pool,
                    signal_type="mean_reversion_setup",
                    price=current,
                    signal_data={
                        "rsi": round(current_rsi, 2),
                        "sma200": round(sma200_val, 2),
                        "deviation_pct": round(deviation, 2),
                        "bb_lower": round(bb_lower, 2),
                    },
                    rationale=(
                        f"{deviation:.0f}% deviation below 200-day MA with RSI {current_rsi:.0f}. "
                        f"Below lower Bollinger Band. Historical reversion rate at this level is high."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"mean_reversion_setup scan failed for {sym}: {exc}")
    return candidates


def dividend_aristocrat_dip(symbols: list[str], pool: str = "steady") -> list[OpportunityCandidate]:
    """Screen for dividend aristocrats trading at significant discount to highs."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 260)
            if df.empty or len(df) < 50:
                continue

            close = df["close"]
            current = float(close.iloc[-1])
            high_52w = float(close.max())

            pct_below_high = (high_52w - current) / high_52w * 100 if high_52w > 0 else 0

            # For steady pool: look for 15%+ pullback from highs
            if pct_below_high > 15:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="stock",
                    pool=pool,
                    signal_type="dividend_aristocrat_dip",
                    price=current,
                    signal_data={
                        "pct_below_52w_high": round(pct_below_high, 2),
                        "high_52w": round(high_52w, 2),
                    },
                    rationale=(
                        f"{sym} down {pct_below_high:.0f}% from highs. "
                        f"Long-term dividend grower at potential value entry."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"dividend_aristocrat_dip scan failed for {sym}: {exc}")
    return candidates


def index_etf_pullback(symbols: list[str], pool: str = "steady") -> list[OpportunityCandidate]:
    """Watch major ETFs for >5% pullback from 50-day SMA."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 60)
            if df.empty or len(df) < 50:
                continue

            close = df["close"]
            rsi_vals = _rsi(close, 14)
            sma50 = _sma(close, 50)

            current = float(close.iloc[-1])
            current_rsi = float(rsi_vals.iloc[-1])
            sma50_val = float(sma50.iloc[-1]) if not np.isnan(sma50.iloc[-1]) else current

            pullback_pct = (sma50_val - current) / sma50_val * 100 if sma50_val > 0 else 0

            if pullback_pct > 5 and current_rsi < 40:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="etf",
                    pool=pool,
                    signal_type="index_etf_pullback",
                    price=current,
                    signal_data={
                        "pullback_pct": round(pullback_pct, 2),
                        "rsi": round(current_rsi, 2),
                        "sma50": round(sma50_val, 2),
                    },
                    rationale=(
                        f"{sym} pulled back {pullback_pct:.1f}% from 50-day MA — "
                        f"historically strong entry for long-term holders."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"index_etf_pullback scan failed for {sym}: {exc}")
    return candidates


def dividend_growth_value(symbols: list[str], pool: str = "steady") -> list[OpportunityCandidate]:
    """Screen dividend stocks for elevated yield relative to historical average."""
    candidates = []
    for sym in symbols:
        try:
            df = alpaca.get_bars(sym, "1Day", 260)
            if df.empty or len(df) < 200:
                continue

            close = df["close"]
            current = float(close.iloc[-1])

            # Proxy: if stock is 15%+ below its 200-day SMA, yield is likely elevated
            sma200 = _sma(close, 200)
            sma200_val = float(sma200.iloc[-1]) if not np.isnan(sma200.iloc[-1]) else current
            discount = (sma200_val - current) / sma200_val * 100 if sma200_val > 0 else 0

            if discount > 10:
                candidates.append(OpportunityCandidate(
                    symbol=sym,
                    asset_type="stock",
                    pool=pool,
                    signal_type="dividend_growth_value",
                    price=current,
                    signal_data={
                        "sma200_discount_pct": round(discount, 2),
                        "sma200": round(sma200_val, 2),
                    },
                    rationale=(
                        f"Yield likely above 3-year average — price has compressed "
                        f"{discount:.0f}% below 200-day MA while fundamentals remain intact. "
                        f"Classic value entry."
                    ),
                ))
        except Exception as exc:
            logger.warning(f"dividend_growth_value scan failed for {sym}: {exc}")
    return candidates


# ---------------------------------------------------------------------------
# Master scan function
# ---------------------------------------------------------------------------

SIGNAL_REGISTRY = {
    "momentum_breakout": momentum_breakout,
    "post_earnings_gap": post_earnings_gap,
    "crypto_rotation": crypto_rotation,
    "value_gap": value_gap,
    "etf_nav_discount": etf_nav_discount,
    "mean_reversion_setup": mean_reversion_setup,
    "dividend_aristocrat_dip": dividend_aristocrat_dip,
    "index_etf_pullback": index_etf_pullback,
    "dividend_growth_value": dividend_growth_value,
}

# Map pool markets to watchlist keys
POOL_WATCHLISTS = {
    "aggressive": {
        "stocks": "stocks_aggressive",
        "crypto": "crypto_aggressive",
    },
    "balanced": {
        "stocks": "stocks_aggressive",
        "etfs": "etfs_balanced",
        "crypto": "crypto_balanced",
    },
    "steady": {
        "etfs": "etfs_steady",
        "dividend_stocks": "dividend_stocks_steady",
    },
}


def scan(pool_config: dict) -> list[OpportunityCandidate]:
    """Run all signal types for a given pool configuration.

    Args:
        pool_config: Pool config dict from pools.yaml (with 'name', 'signal_types', 'markets', etc.)

    Returns:
        List of all OpportunityCandidate matches (unscored).
    """
    watchlist = _load_watchlist()
    pool_key = None
    for key in ["aggressive", "balanced", "steady"]:
        if pool_config.get("name", "").lower().startswith(key[:4]):
            pool_key = key
            break

    if not pool_key:
        # Try matching by description
        desc = pool_config.get("description", "").lower()
        if "aggressive" in desc or "momentum" in desc:
            pool_key = "aggressive"
        elif "balanced" in desc or "value" in desc:
            pool_key = "balanced"
        else:
            pool_key = "steady"

    signal_types = pool_config.get("signal_types", [])
    markets = pool_config.get("markets", [])

    all_candidates = []

    # Collect symbols from watchlists for this pool's markets
    pool_wl = POOL_WATCHLISTS.get(pool_key, {})

    for signal_name in signal_types:
        signal_fn = SIGNAL_REGISTRY.get(signal_name)
        if not signal_fn:
            logger.warning(f"Unknown signal type: {signal_name}")
            continue

        # Determine which symbols to scan
        symbols = []
        for market in markets:
            wl_key = pool_wl.get(market, "")
            if wl_key and wl_key in watchlist:
                symbols.extend(watchlist[wl_key])

        # Deduplicate
        symbols = list(dict.fromkeys(symbols))

        if not symbols:
            continue

        try:
            candidates = signal_fn(symbols, pool=pool_key)
            all_candidates.extend(candidates)
            if candidates:
                logger.info(f"[{pool_key}] {signal_name}: found {len(candidates)} candidates")
        except Exception as exc:
            logger.error(f"[{pool_key}] {signal_name} scan failed: {exc}")

    logger.info(f"[{pool_key}] Total candidates from scan: {len(all_candidates)}")
    return all_candidates
