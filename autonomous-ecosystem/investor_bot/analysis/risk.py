"""Risk calculations and position sizing (informational only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from shared.database import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
_POOLS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "investment" / "pools.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _load_pools() -> dict:
    with open(_POOLS_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def suggested_position_size(
    pool: str,
    score: float,
    price: float,
    pool_cash: float | None = None,
) -> dict:
    """Calculate suggested position size using half-Kelly criterion.

    Returns:
        dict with suggested_usd, suggested_shares_or_units, pct_of_pool
    """
    cfg = _load_config()
    pools_cfg = _load_pools()
    pool_cfg = pools_cfg.get("pools", {}).get(pool, {})

    total_capital = cfg.get("investor_bot", {}).get("total_capital_usd", 10000)
    alloc_pct = pool_cfg.get("allocation_pct", 33)
    pool_capital = total_capital * alloc_pct / 100
    max_position_pct = pool_cfg.get("max_position_pct", 10) / 100

    # Calculate pool cash if not provided
    if pool_cash is None:
        positions = db.get_positions_by_pool(pool)
        positions_value = sum(
            float(p.get("quantity", 0)) * float(p.get("current_price", 0))
            for p in positions
        )
        pool_cash = pool_capital - positions_value

    # Half-Kelly: f = (p*b - q) / b, then halve it
    # Simplified: use score as win probability proxy
    win_prob = min(score, 0.85)  # cap at 85%
    avg_win = 0.15  # assume 15% avg win
    avg_loss = 0.08  # assume 8% avg loss
    odds = avg_win / avg_loss if avg_loss > 0 else 2.0

    kelly_fraction = (win_prob * odds - (1 - win_prob)) / odds
    kelly_fraction = max(kelly_fraction, 0.01)
    half_kelly = kelly_fraction / 2

    # Cap at max position percentage
    position_pct = min(half_kelly, max_position_pct)

    suggested_usd = min(pool_capital * position_pct, pool_cash * 0.9)  # leave 10% cash buffer
    suggested_usd = max(suggested_usd, 0)

    if price > 0:
        suggested_units = suggested_usd / price
    else:
        suggested_units = 0

    return {
        "suggested_usd": round(suggested_usd, 2),
        "suggested_shares_or_units": round(suggested_units, 4),
        "pct_of_pool": round(position_pct * 100, 2),
        "pool_capital": round(pool_capital, 2),
        "pool_cash": round(pool_cash, 2),
        "half_kelly_raw": round(half_kelly, 4),
    }


# ---------------------------------------------------------------------------
# Stop loss estimation
# ---------------------------------------------------------------------------

def estimate_stop_loss(
    signal_type: str,
    price: float,
    signal_data: dict,
) -> float:
    """Estimate a stop-loss price based on signal type.

    Returns:
        Suggested stop-loss price.
    """
    if signal_type == "momentum_breakout":
        # Stop below EMA(20)
        ema20 = signal_data.get("ema20", price * 0.95)
        return round(float(ema20) * 0.98, 4)  # 2% below EMA(20)

    elif signal_type in ("value_gap", "mean_reversion_setup"):
        # Stop below recent support (52-week low or lower BB)
        low = signal_data.get("low_52w", signal_data.get("bb_lower", price * 0.85))
        return round(float(low) * 0.97, 4)  # 3% below support

    elif signal_type in ("crypto_rotation",):
        # 15-20% below entry for crypto
        return round(price * 0.82, 4)

    elif signal_type in ("post_earnings_gap",):
        pre_earnings = signal_data.get("pre_earnings_close", price * 0.95)
        return round(float(pre_earnings) * 0.95, 4)

    elif signal_type in ("dividend_aristocrat_dip", "dividend_growth_value"):
        # Stop below 52-week low
        low = signal_data.get("low_52w", price * 0.85)
        return round(float(low) * 0.95 if low else price * 0.88, 4)

    elif signal_type in ("index_etf_pullback", "etf_nav_discount"):
        sma50 = signal_data.get("sma50", price * 0.90)
        return round(price * 0.92, 4)

    # Default: 10% stop
    return round(price * 0.90, 4)


# ---------------------------------------------------------------------------
# Price target estimation
# ---------------------------------------------------------------------------

def estimate_price_target(
    signal_type: str,
    price: float,
    signal_data: dict,
) -> float:
    """Estimate price target based on signal type.

    Returns:
        Estimated target price.
    """
    if signal_type == "momentum_breakout":
        # Measured move: project the breakout range
        return round(price * 1.15, 4)  # 15% upside target

    elif signal_type == "mean_reversion_setup":
        # Return to 200-day SMA
        sma200 = signal_data.get("sma200", price * 1.10)
        return round(float(sma200), 4)

    elif signal_type == "value_gap":
        # Return toward 52-week high (partial)
        high = signal_data.get("high_52w", price * 1.25)
        return round(float(high) * 0.85, 4)  # Target 85% of high

    elif signal_type == "crypto_rotation":
        # 30-50% upside target for altcoin rotation
        return round(price * 1.35, 4)

    elif signal_type == "post_earnings_gap":
        # Target: pre-earnings + full earnings gap
        gap_pct = signal_data.get("gap_pct", 5)
        return round(price * (1 + float(gap_pct) / 100), 4)

    elif signal_type == "etf_nav_discount":
        prev_close = signal_data.get("prev_close", price * 1.01)
        return round(float(prev_close), 4)

    elif signal_type in ("dividend_aristocrat_dip", "dividend_growth_value"):
        # Target: return toward 200-day SMA
        sma200 = signal_data.get("sma200", price * 1.15)
        return round(float(sma200) * 0.95 if sma200 else price * 1.12, 4)

    elif signal_type == "index_etf_pullback":
        sma50 = signal_data.get("sma50", price * 1.05)
        return round(float(sma50), 4)

    # Default: 12% target
    return round(price * 1.12, 4)


# ---------------------------------------------------------------------------
# Risk/reward ratio
# ---------------------------------------------------------------------------

def risk_reward_ratio(entry: float, target: float, stop: float) -> float:
    """Calculate risk/reward ratio.

    Returns:
        Ratio (target - entry) / (entry - stop). Flagged if < 2.0.
    """
    risk = entry - stop
    reward = target - entry
    if risk <= 0:
        return 0.0
    ratio = reward / risk
    if ratio < 2.0:
        logger.warning(f"Poor risk/reward: {ratio:.1f}:1 (entry={entry}, target={target}, stop={stop})")
    return round(ratio, 2)


# ---------------------------------------------------------------------------
# Pool health check
# ---------------------------------------------------------------------------

def pool_health_check(pool: str) -> dict:
    """Check current pool health status.

    Returns:
        dict with cash_pct, position_count, largest_position_pct,
        drawdown_from_peak, is_healthy
    """
    cfg = _load_config()
    pools_cfg = _load_pools()
    pool_cfg = pools_cfg.get("pools", {}).get(pool, {})

    total_capital = cfg.get("investor_bot", {}).get("total_capital_usd", 10000)
    alloc_pct = pool_cfg.get("allocation_pct", 33)
    pool_capital = total_capital * alloc_pct / 100

    positions = db.get_positions_by_pool(pool)
    positions_value = sum(
        float(p.get("quantity", 0)) * float(p.get("current_price", 0))
        for p in positions
    )

    total_value = pool_capital  # cash + positions
    cash = pool_capital - positions_value
    cash_pct = (cash / pool_capital * 100) if pool_capital > 0 else 100

    # Largest position
    largest_pct = 0.0
    for p in positions:
        pos_value = float(p.get("quantity", 0)) * float(p.get("current_price", 0))
        pct = (pos_value / pool_capital * 100) if pool_capital > 0 else 0
        largest_pct = max(largest_pct, pct)

    # Drawdown from peak (using pool snapshots)
    snapshots = db.execute_query(
        "SELECT MAX(total_value_usd) as peak FROM pool_snapshots WHERE pool = %s",
        (pool,),
    )
    peak = float(snapshots[0]["peak"]) if snapshots and snapshots[0]["peak"] else pool_capital
    drawdown = ((peak - total_value) / peak * 100) if peak > 0 else 0

    is_healthy = drawdown < 20 and cash_pct > 5

    return {
        "pool": pool,
        "pool_capital": round(pool_capital, 2),
        "total_value": round(total_value, 2),
        "cash_usd": round(cash, 2),
        "cash_pct": round(cash_pct, 2),
        "positions_value": round(positions_value, 2),
        "position_count": len(positions),
        "largest_position_pct": round(largest_pct, 2),
        "drawdown_from_peak": round(drawdown, 2),
        "is_healthy": is_healthy,
    }


# ---------------------------------------------------------------------------
# Combined risk calculation for alerts
# ---------------------------------------------------------------------------

def calculate(candidate: Any, pool_config: dict) -> dict:
    """Calculate all risk metrics for a candidate.

    Returns combined dict for use in trade alerts.
    """
    pool = candidate.pool
    price = candidate.price
    signal_data = candidate.signal_data
    signal_type = candidate.signal_type

    # Position sizing
    from investor_bot.analysis.scorer import ScoreResult
    score_val = signal_data.get("_score", 0.65)
    sizing = suggested_position_size(pool, score_val, price)

    # Stop and target
    stop = estimate_stop_loss(signal_type, price, signal_data)
    target = estimate_price_target(signal_type, price, signal_data)
    rr = risk_reward_ratio(price, target, stop)

    return {
        "suggested_usd": sizing["suggested_usd"],
        "suggested_shares_or_units": sizing["suggested_shares_or_units"],
        "pct_of_pool": sizing["pct_of_pool"],
        "stop_loss": stop,
        "target": target,
        "rr_ratio": rr,
        "pool_capital": sizing["pool_capital"],
        "pool_cash": sizing["pool_cash"],
    }
