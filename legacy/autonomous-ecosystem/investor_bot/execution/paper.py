"""Paper trading simulation engine.

All trades are simulated locally using the database.
No real brokerage calls are made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from investor_bot.data import alpaca, crypto
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
# Price fetching
# ---------------------------------------------------------------------------

def _get_current_price(symbol: str, asset_type: str) -> float:
    """Fetch current market price for any asset type."""
    if asset_type == "crypto":
        return crypto.get_price(symbol)
    else:
        return alpaca.get_price(symbol)


# ---------------------------------------------------------------------------
# Paper trade execution
# ---------------------------------------------------------------------------

def execute_buy(
    pool: str,
    symbol: str,
    asset_type: str,
    quantity: float | None = None,
    notional_usd: float | None = None,
) -> dict:
    """Simulate a market buy order.

    Either quantity or notional_usd must be provided.

    Returns:
        dict with fill details: symbol, quantity, fill_price, total_cost, pool, cash_remaining
    """
    price = _get_current_price(symbol, asset_type)
    if price <= 0:
        raise ValueError(f"Cannot get price for {symbol}")

    if notional_usd and not quantity:
        quantity = notional_usd / price
    elif not quantity:
        raise ValueError("Must provide quantity or notional_usd")

    total_cost = quantity * price

    # Check pool has enough cash
    pool_cash = get_pool_cash(pool)
    if total_cost > pool_cash:
        raise ValueError(
            f"Insufficient cash in {pool} pool: need ${total_cost:.2f}, have ${pool_cash:.2f}"
        )

    # Upsert position
    db.upsert_position(
        pool=pool,
        symbol=symbol,
        asset_type=asset_type,
        quantity=quantity,
        avg_cost=price,
        current_price=price,
    )

    logger.info(
        f"Paper BUY: {quantity:.4f} {symbol} @ ${price:.4f} = ${total_cost:.2f} "
        f"[{pool} pool]"
    )

    return {
        "symbol": symbol,
        "quantity": round(quantity, 8),
        "fill_price": round(price, 4),
        "total_cost": round(total_cost, 2),
        "pool": pool,
        "cash_remaining": round(pool_cash - total_cost, 2),
    }


def execute_sell(
    pool: str,
    symbol: str,
    asset_type: str,
    quantity: float,
) -> dict:
    """Simulate a market sell order.

    Returns:
        dict with fill details.
    """
    price = _get_current_price(symbol, asset_type)
    if price <= 0:
        raise ValueError(f"Cannot get price for {symbol}")

    total_proceeds = quantity * price

    result = db.reduce_position(pool, symbol, quantity)
    if result is None:
        raise ValueError(f"No position found for {symbol} in {pool} pool")

    logger.info(
        f"Paper SELL: {quantity:.4f} {symbol} @ ${price:.4f} = ${total_proceeds:.2f} "
        f"[{pool} pool]"
    )

    return {
        "symbol": symbol,
        "quantity": round(quantity, 8),
        "fill_price": round(price, 4),
        "total_proceeds": round(total_proceeds, 2),
        "pool": pool,
        "position_closed": result.get("closed", False),
    }


# ---------------------------------------------------------------------------
# Portfolio valuation
# ---------------------------------------------------------------------------

def get_pool_cash(pool: str) -> float:
    """Calculate remaining cash in a pool."""
    cfg = _load_config()
    pools_cfg = _load_pools()
    pool_cfg = pools_cfg.get("pools", {}).get(pool, {})

    total_capital = cfg.get("investor_bot", {}).get("total_capital_usd", 10000)
    alloc_pct = pool_cfg.get("allocation_pct", 33)
    pool_capital = total_capital * alloc_pct / 100

    positions = db.get_positions_by_pool(pool)
    # Cash = pool capital - sum of position costs (not current value)
    total_invested = sum(
        float(p.get("quantity", 0)) * float(p.get("avg_cost", 0))
        for p in positions
    )
    # Add back any realized gains from sells (tracked implicitly)
    return max(pool_capital - total_invested, 0)


def get_pool_value(pool: str) -> dict:
    """Get full portfolio valuation for a pool.

    Returns:
        dict with total_value, cash, positions_value, positions list with P&L
    """
    cfg = _load_config()
    pools_cfg = _load_pools()
    pool_cfg = pools_cfg.get("pools", {}).get(pool, {})

    total_capital = cfg.get("investor_bot", {}).get("total_capital_usd", 10000)
    alloc_pct = pool_cfg.get("allocation_pct", 33)
    pool_capital = total_capital * alloc_pct / 100

    positions = db.get_positions_by_pool(pool)
    position_details = []
    total_positions_value = 0.0
    total_cost_basis = 0.0

    for p in positions:
        symbol = p["symbol"]
        asset_type = p["asset_type"]
        qty = float(p["quantity"])
        avg_cost = float(p["avg_cost"])

        # Fetch current price
        current_price = _get_current_price(symbol, asset_type)
        if current_price <= 0:
            current_price = float(p.get("current_price", avg_cost))

        # Update position with current price
        db.execute_write(
            "UPDATE positions SET current_price = %s, last_updated = NOW() WHERE pool = %s AND symbol = %s",
            (current_price, pool, symbol),
        )

        market_value = qty * current_price
        cost_basis = qty * avg_cost
        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_pct = ((current_price / avg_cost) - 1) * 100 if avg_cost > 0 else 0

        total_positions_value += market_value
        total_cost_basis += cost_basis

        position_details.append({
            "symbol": symbol,
            "asset_type": asset_type,
            "quantity": round(qty, 8),
            "avg_cost": round(avg_cost, 4),
            "current_price": round(current_price, 4),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
        })

    cash = pool_capital - total_cost_basis
    total_value = cash + total_positions_value

    # Take snapshot
    try:
        db.insert_pool_snapshot(pool, total_value, cash, total_positions_value)
    except Exception as exc:
        logger.warning(f"Failed to save pool snapshot: {exc}")

    return {
        "pool": pool,
        "pool_name": pool_cfg.get("name", pool),
        "starting_capital": round(pool_capital, 2),
        "total_value": round(total_value, 2),
        "cash": round(cash, 2),
        "cash_pct": round((cash / pool_capital * 100) if pool_capital > 0 else 100, 2),
        "positions_value": round(total_positions_value, 2),
        "total_pnl": round(total_value - pool_capital, 2),
        "total_pnl_pct": round(((total_value / pool_capital) - 1) * 100, 2) if pool_capital > 0 else 0,
        "positions": position_details,
    }


def get_all_pools_value() -> dict:
    """Get portfolio valuation for all pools combined."""
    results = {}
    total_value = 0.0
    total_capital = 0.0

    for pool_name in ["aggressive", "balanced", "steady"]:
        pv = get_pool_value(pool_name)
        results[pool_name] = pv
        total_value += pv["total_value"]
        total_capital += pv["starting_capital"]

    return {
        "pools": results,
        "total_value": round(total_value, 2),
        "total_capital": round(total_capital, 2),
        "total_pnl": round(total_value - total_capital, 2),
        "total_pnl_pct": round(((total_value / total_capital) - 1) * 100, 2) if total_capital > 0 else 0,
    }


def check_position_drawdowns(pool: str, threshold_pct: float = 15.0) -> list[dict]:
    """Check for positions with unrealized loss exceeding threshold.

    Returns list of positions that are down more than threshold_pct.
    """
    positions = db.get_positions_by_pool(pool)
    alerts = []

    for p in positions:
        avg_cost = float(p["avg_cost"])
        symbol = p["symbol"]
        asset_type = p["asset_type"]

        current_price = _get_current_price(symbol, asset_type)
        if current_price <= 0:
            continue

        pnl_pct = ((current_price / avg_cost) - 1) * 100 if avg_cost > 0 else 0

        if pnl_pct < -threshold_pct:
            alerts.append({
                "symbol": symbol,
                "pool": pool,
                "avg_cost": round(avg_cost, 4),
                "current_price": round(current_price, 4),
                "loss_pct": round(abs(pnl_pct), 2),
            })

    return alerts
