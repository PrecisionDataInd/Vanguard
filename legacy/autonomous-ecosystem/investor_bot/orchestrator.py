"""Investor Bot orchestrator — main entry point with three async scan loops."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from investor_bot.analysis import risk, scorer, screener
from investor_bot.execution import paper
from shared.alerts import telegram
from shared.database import db
from shared.utils.logging import setup_logging

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
_POOLS_PATH = Path(__file__).resolve().parent.parent / "config" / "investment" / "pools.yaml"

_running = True
_pool_halted: dict[str, bool] = {"aggressive": False, "balanced": False, "steady": False}


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _load_pools() -> dict:
    with open(_POOLS_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Trade execution (on approval)
# ---------------------------------------------------------------------------

async def process_approved_trades() -> None:
    """Check for approved trade alerts and execute paper trades."""
    try:
        approved = db.execute_query(
            """
            SELECT ta.*, ar.payload
            FROM trade_alerts ta
            JOIN approval_requests ar ON ta.approval_id = ar.id
            WHERE ta.status = 'approved'
              AND ar.status = 'approved'
            ORDER BY ta.created_at ASC
            """
        )

        for alert in approved:
            try:
                symbol = alert["symbol"]
                pool = alert["pool"]
                action = alert["action"]
                asset_type = alert["asset_type"]
                notional = float(alert["notional_usd"]) if alert["notional_usd"] else 0

                if action == "BUY":
                    fill = paper.execute_buy(
                        pool=pool,
                        symbol=symbol,
                        asset_type=asset_type,
                        notional_usd=notional,
                    )
                    db.update_trade_alert_status(alert["id"], "executed")
                    await telegram.send_message(
                        f"Paper trade recorded: BUY "
                        f"{fill['quantity']:.4f} {symbol} @ ${fill['fill_price']:.4f} "
                        f"= ${fill['total_cost']:.2f}\n"
                        f"Pool cash remaining: ${fill['cash_remaining']:.2f}"
                    )

                elif action == "SELL":
                    qty = float(alert["quantity"]) if alert["quantity"] else 0
                    fill = paper.execute_sell(
                        pool=pool,
                        symbol=symbol,
                        asset_type=asset_type,
                        quantity=qty,
                    )
                    db.update_trade_alert_status(alert["id"], "executed")
                    await telegram.send_message(
                        f"Paper trade recorded: SELL "
                        f"{fill['quantity']:.4f} {symbol} @ ${fill['fill_price']:.4f} "
                        f"= ${fill['total_proceeds']:.2f}"
                    )

            except Exception as exc:
                logger.error(f"Failed to execute approved trade {alert['id']}: {exc}")
                db.update_trade_alert_status(alert["id"], "failed")
                await telegram.send_message(
                    f"Paper trade execution failed for {alert.get('symbol', '?')}: {exc}"
                )

    except Exception as exc:
        logger.error(f"process_approved_trades error: {exc}")


# ---------------------------------------------------------------------------
# Scan loops
# ---------------------------------------------------------------------------

async def _scan_loop(pool_key: str) -> None:
    """Generic scan loop for a pool."""
    pools_cfg = _load_pools()
    pool_config = pools_cfg.get("pools", {}).get(pool_key, {})
    interval_minutes = pool_config.get("scan_interval_minutes", 60)
    min_score = pool_config.get("min_score_to_alert", 0.60)

    cfg = _load_config()
    max_alerts = cfg.get("investor_bot", {}).get("max_alerts_per_scan", 3)
    cooldown_hours = cfg.get("investor_bot", {}).get("alert_cooldown_hours", 6)

    logger.info(f"[{pool_key}] Scan loop started (interval: {interval_minutes}m)")

    while _running:
        try:
            # Check paused state
            if telegram.is_investor_paused() or _pool_halted.get(pool_key, False):
                await asyncio.sleep(60)
                continue

            # Process any approved trades first
            await process_approved_trades()

            # Check pool drawdown
            health = risk.pool_health_check(pool_key)
            if health["drawdown_from_peak"] >= 20:
                if not _pool_halted[pool_key]:
                    _pool_halted[pool_key] = True
                    await telegram.send_message(
                        f"{pool_config.get('name', pool_key)} paper portfolio down 20%. "
                        f"Scanning paused. /resume to restart."
                    )
                    logger.warning(f"[{pool_key}] Pool halted due to 20% drawdown")
                await asyncio.sleep(60)
                continue

            # Check existing positions for drawdown alerts
            drawdown_alerts = paper.check_position_drawdowns(pool_key, threshold_pct=15.0)
            for da in drawdown_alerts:
                await telegram.send_message(
                    f"{da['symbol']} down {da['loss_pct']:.1f}% from entry in {pool_key} pool. "
                    f"Consider reviewing position."
                )

            # Run screener
            logger.info(f"[{pool_key}] Starting scan...")
            candidates = screener.scan(pool_config)

            # Score and filter
            alerts_sent = 0
            for candidate in candidates:
                if alerts_sent >= max_alerts:
                    break

                # Check cooldown
                if db.check_alert_cooldown(candidate.symbol, cooldown_hours):
                    logger.debug(f"[{pool_key}] {candidate.symbol} in cooldown, skipping")
                    continue

                # Score
                candidate.signal_data["_score"] = 0.65  # seed for risk calc
                score_result = scorer.score(candidate, min_score=min_score)
                if score_result is None:
                    continue

                # Update score in signal_data for risk calc
                candidate.signal_data["_score"] = score_result.score

                # Calculate risk
                risk_info = risk.calculate(candidate, pool_config)

                # Create approval request
                payload = {
                    "symbol": candidate.symbol,
                    "asset_type": candidate.asset_type,
                    "pool": candidate.pool,
                    "action": candidate.action,
                    "signal_type": candidate.signal_type,
                    "price": candidate.price,
                    "rationale": candidate.rationale,
                    "signal_data": candidate.signal_data,
                    "score": score_result.score,
                    "conviction": score_result.conviction,
                    "risk_info": risk_info,
                }

                approval = db.insert_approval_request(
                    system="investor_bot",
                    req_type="trade",
                    payload=payload,
                )

                # Insert trade alert record
                db.insert_trade_alert(
                    approval_id=str(approval["id"]),
                    pool=candidate.pool,
                    action=candidate.action,
                    symbol=candidate.symbol,
                    asset_type=candidate.asset_type,
                    quantity=risk_info["suggested_shares_or_units"],
                    price_at_signal=candidate.price,
                    notional_usd=risk_info["suggested_usd"],
                    signal_type=candidate.signal_type,
                    score=score_result.score,
                    rationale=candidate.rationale,
                    status="pending",
                )

                # Send Telegram alert
                await telegram.send_trade_alert(
                    candidate={
                        "symbol": candidate.symbol,
                        "asset_type": candidate.asset_type,
                        "pool": candidate.pool,
                        "action": candidate.action,
                        "signal_type": candidate.signal_type,
                        "price": candidate.price,
                        "rationale": candidate.rationale,
                    },
                    score_result={
                        "score": score_result.score,
                        "conviction": score_result.conviction,
                    },
                    risk_info=risk_info,
                    approval_id=str(approval["id"]),
                )

                alerts_sent += 1
                logger.info(
                    f"[{pool_key}] Alert sent: {candidate.action} {candidate.symbol} "
                    f"(score={score_result.score:.3f}, conviction={score_result.conviction})"
                )

            logger.info(f"[{pool_key}] Scan complete. {alerts_sent} alerts sent.")

        except Exception as exc:
            logger.error(f"[{pool_key}] Scan loop error: {exc}")

        await asyncio.sleep(interval_minutes * 60)


async def scan_loop_aggressive() -> None:
    await _scan_loop("aggressive")


async def scan_loop_balanced() -> None:
    await _scan_loop("balanced")


async def scan_loop_steady() -> None:
    await _scan_loop("steady")


# ---------------------------------------------------------------------------
# Expiry loop
# ---------------------------------------------------------------------------

async def expiry_loop() -> None:
    """Periodically expire old approvals."""
    while _running:
        try:
            await telegram.expire_old_approvals()
        except Exception as exc:
            logger.error(f"Expiry loop error: {exc}")
        await asyncio.sleep(300)  # check every 5 minutes


# ---------------------------------------------------------------------------
# Trade approval processor loop
# ---------------------------------------------------------------------------

async def approval_processor_loop() -> None:
    """Continuously check for newly approved trades and execute them."""
    while _running:
        try:
            await process_approved_trades()
        except Exception as exc:
            logger.error(f"Approval processor error: {exc}")
        await asyncio.sleep(15)  # check every 15 seconds


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Main entry point for the investor bot."""
    global _running

    setup_logging("investor_bot")

    cfg = _load_config()
    if not cfg["mode"].get("investor_bot_enabled", False):
        logger.info("Investor bot disabled in config. Exiting.")
        return

    # Initialize infrastructure
    logger.info("Initializing database...")
    db.init_db()
    db.init_redis()

    # Run schema
    try:
        db.run_schema()
    except Exception as exc:
        logger.warning(f"Schema init (may already exist): {exc}")

    # Initialize Telegram bot
    logger.info("Initializing Telegram bot...")
    app = await telegram.init_telegram_bot()

    # Send startup message
    await telegram.send_startup_message()

    logger.info("Investor Bot starting scan loops...")

    # Safety: all trades are paper only
    assert cfg["mode"].get("paper_trading", True), \
        "Paper trading must be enabled — no live trades in this build"

    try:
        # Run all loops concurrently
        await asyncio.gather(
            scan_loop_aggressive(),
            scan_loop_balanced(),
            scan_loop_steady(),
            expiry_loop(),
            approval_processor_loop(),
            app.run_polling(drop_pending_updates=True),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down investor bot...")
        _running = False
    except Exception as exc:
        logger.error(f"Fatal error in investor bot: {exc}")
        _running = False


def run() -> None:
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
