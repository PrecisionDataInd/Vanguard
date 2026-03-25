"""Unified Telegram bot with approval flow for deals and trades."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
# Module state
# ---------------------------------------------------------------------------

_app: Application | None = None
_chat_id: str = ""
_paused_deals: bool = False
_paused_investor: bool = False
_awaiting_note: dict[int, str] = {}  # user_id -> trade_alert_id


# ---------------------------------------------------------------------------
# Bot initialization
# ---------------------------------------------------------------------------

async def init_telegram_bot() -> Application:
    """Create and configure the Telegram bot application."""
    global _app, _chat_id

    cfg = _load_config()
    token = cfg["api_keys"]["telegram_bot_token"]
    _chat_id = str(cfg["api_keys"]["telegram_chat_id"])

    _app = Application.builder().token(token).build()

    # Command handlers
    _app.add_handler(CommandHandler("status", cmd_status))
    _app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    _app.add_handler(CommandHandler("deals", cmd_deals))
    _app.add_handler(CommandHandler("alerts", cmd_alerts))
    _app.add_handler(CommandHandler("pause", cmd_pause))
    _app.add_handler(CommandHandler("resume", cmd_resume))
    _app.add_handler(CommandHandler("pause_deals", cmd_pause_deals))
    _app.add_handler(CommandHandler("pause_investor", cmd_pause_investor))
    _app.add_handler(CommandHandler("start", cmd_start))
    _app.add_handler(CommandHandler("help", cmd_help))

    # Callback query handler for inline buttons
    _app.add_handler(CallbackQueryHandler(handle_callback))

    # Message handler for notes
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Telegram bot initialized")
    return _app


def get_app() -> Application | None:
    return _app


def get_chat_id() -> str:
    return _chat_id


def is_deals_paused() -> bool:
    return _paused_deals


def is_investor_paused() -> bool:
    return _paused_investor


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

async def send_message(text: str, reply_markup: InlineKeyboardMarkup | None = None) -> int | None:
    """Send a message to the configured chat. Returns message_id."""
    if not _app or not _chat_id:
        logger.error("Telegram bot not initialized")
        return None
    try:
        msg = await _app.bot.send_message(
            chat_id=_chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return msg.message_id
    except Exception as exc:
        logger.error(f"Failed to send Telegram message: {exc}")
        return None


async def edit_message_buttons(
    message_id: int,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Edit inline keyboard on an existing message."""
    if not _app or not _chat_id:
        return False
    try:
        await _app.bot.edit_message_reply_markup(
            chat_id=_chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
        return True
    except Exception as exc:
        logger.warning(f"Failed to edit message buttons: {exc}")
        return False


# ---------------------------------------------------------------------------
# Deal approval alerts
# ---------------------------------------------------------------------------

async def send_deal_alert(deal: dict, approval_id: str) -> int | None:
    """Send a deal approval request to Telegram.

    deal dict expected keys: title, marketplace, category, price, estimated_value,
    discount_pct, score, boosters, flags, listing_url
    """
    title = deal.get("title", "Unknown")[:60]
    marketplace = deal.get("marketplace", "Unknown")
    category = deal.get("category", "N/A")
    price = deal.get("price", 0)
    est_value = deal.get("estimated_value", 0)
    discount = deal.get("discount_pct", 0)
    score = deal.get("score", 0)
    boosters = deal.get("boosters", "")
    flags = deal.get("flags", "")
    url = deal.get("listing_url", "")

    text = (
        f"<b>DEAL — APPROVAL REQUIRED</b>\n\n"
        f"<b>{title}</b>\n"
        f"Source: {marketplace}\n"
        f"{category}\n"
        f"Asking: <b>${price:.2f}</b>\n"
        f"Est. Value: ~${est_value:.2f} ({discount:.0f}% below market)\n"
        f"Score: <b>{score:.0f}%</b>\n"
    )
    if boosters:
        text += f"{boosters}\n"
    if flags:
        text += f"{flags}\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("BUY IT", callback_data=f"deal_approve:{approval_id}"),
            InlineKeyboardButton("SKIP", callback_data=f"deal_reject:{approval_id}"),
        ],
        [
            InlineKeyboardButton("VIEW", url=url) if url else InlineKeyboardButton("VIEW", callback_data="noop"),
            InlineKeyboardButton("REMIND ME IN 2H", callback_data=f"deal_remind:{approval_id}"),
        ],
    ])

    msg_id = await send_message(text, keyboard)
    if msg_id:
        db.update_approval_status(approval_id, "pending", telegram_msg_id=msg_id)
    return msg_id


# ---------------------------------------------------------------------------
# Trade approval alerts
# ---------------------------------------------------------------------------

async def send_trade_alert(
    candidate: dict,
    score_result: dict,
    risk_info: dict,
    approval_id: str,
) -> int | None:
    """Send a trade alert to Telegram for approval.

    candidate keys: symbol, asset_type, pool, signal_type, price, rationale
    score_result keys: score, conviction
    risk_info keys: suggested_usd, suggested_shares_or_units, stop_loss, target, rr_ratio
    """
    pool = candidate.get("pool", "unknown")
    pool_name = pool.upper()
    action = candidate.get("action", "BUY")
    symbol = candidate.get("symbol", "???")
    asset_type = candidate.get("asset_type", "stock")
    notional = risk_info.get("suggested_usd", 0)
    signal_type = candidate.get("signal_type", "unknown")
    score = score_result.get("score", 0)
    conviction = score_result.get("conviction", "LOW")
    rationale = candidate.get("rationale", "No rationale provided.")
    price = candidate.get("price", 0)

    text = (
        f"<b>TRADE ALERT — {pool_name} POOL</b>\n\n"
        f"<b>{action} {symbol}</b> ({asset_type})\n"
        f"Estimated size: ~<b>${notional:.2f}</b>\n"
        f"Signal: {signal_type}\n"
        f"Score: <b>{score:.0%}</b> | Conviction: <b>{conviction}</b>\n"
        f"Rationale: {rationale}\n\n"
        f"Current price: ${price:.4f}\n"
        f"Suggested entry: market order\n"
    )

    if risk_info.get("stop_loss"):
        text += f"Stop loss: ${risk_info['stop_loss']:.4f}\n"
    if risk_info.get("target"):
        text += f"Target: ${risk_info['target']:.4f}\n"
    if risk_info.get("rr_ratio"):
        rr = risk_info["rr_ratio"]
        rr_warning = " (poor R:R)" if rr < 2.0 else ""
        text += f"Risk/Reward: {rr:.1f}:1{rr_warning}\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("EXECUTE", callback_data=f"trade_approve:{approval_id}"),
            InlineKeyboardButton("PASS", callback_data=f"trade_reject:{approval_id}"),
        ],
        [
            InlineKeyboardButton("MORE INFO", callback_data=f"trade_info:{approval_id}"),
            InlineKeyboardButton("NOTE", callback_data=f"trade_note:{approval_id}"),
        ],
    ])

    msg_id = await send_message(text, keyboard)
    if msg_id:
        db.update_approval_status(approval_id, "pending", telegram_msg_id=msg_id)
    return msg_id


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    data = query.data
    if data == "noop":
        return

    parts = data.split(":", 1)
    if len(parts) != 2:
        return

    action, approval_id = parts[0], parts[1]

    if action == "deal_approve":
        await _handle_deal_approve(query, approval_id)
    elif action == "deal_reject":
        await _handle_deal_reject(query, approval_id)
    elif action == "deal_remind":
        await _handle_deal_remind(query, approval_id, context)
    elif action == "trade_approve":
        await _handle_trade_approve(query, approval_id)
    elif action == "trade_reject":
        await _handle_trade_reject(query, approval_id)
    elif action == "trade_info":
        await _handle_trade_info(query, approval_id)
    elif action == "trade_note":
        await _handle_trade_note_prompt(query, approval_id)


async def _handle_deal_approve(query: Any, approval_id: str) -> None:
    db.update_approval_status(approval_id, "approved")
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("APPROVED — Processing...", callback_data="noop")]
        ])
    )
    await send_message("Approved! Starting purchase flow...")
    logger.info(f"Deal {approval_id} approved via Telegram")


async def _handle_deal_reject(query: Any, approval_id: str) -> None:
    db.update_approval_status(approval_id, "rejected")
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("SKIPPED", callback_data="noop")]
        ])
    )
    logger.info(f"Deal {approval_id} rejected via Telegram")


async def _handle_deal_remind(query: Any, approval_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Schedule a re-alert in 2 hours."""
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Reminder set for 2h", callback_data="noop")]
        ])
    )

    async def _remind(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        rows = db.execute_query(
            "SELECT * FROM approval_requests WHERE id = %s", (approval_id,)
        )
        if rows and rows[0]["status"] == "pending":
            payload = rows[0]["payload"]
            await send_deal_alert(payload, approval_id)

    context.job_queue.run_once(_remind, when=7200, name=f"remind_{approval_id}")
    logger.info(f"Deal {approval_id} reminder scheduled for 2h")


async def _handle_trade_approve(query: Any, approval_id: str) -> None:
    db.update_approval_status(approval_id, "approved")
    alert = db.get_trade_alert_by_approval(approval_id)
    if alert:
        db.update_trade_alert_status(alert["id"], "approved")
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("EXECUTING...", callback_data="noop")]
        ])
    )
    logger.info(f"Trade {approval_id} approved via Telegram")


async def _handle_trade_reject(query: Any, approval_id: str) -> None:
    db.update_approval_status(approval_id, "rejected")
    alert = db.get_trade_alert_by_approval(approval_id)
    if alert:
        db.update_trade_alert_status(alert["id"], "rejected")
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("PASSED", callback_data="noop")]
        ])
    )
    logger.info(f"Trade {approval_id} rejected via Telegram")


async def _handle_trade_info(query: Any, approval_id: str) -> None:
    """Send extended info about a trade alert."""
    alert = db.get_trade_alert_by_approval(approval_id)
    if not alert:
        await send_message("Trade alert not found.")
        return

    symbol = alert["symbol"]
    pool = alert["pool"]

    # Build info from what we have in the alert payload
    approval_rows = db.execute_query(
        "SELECT payload FROM approval_requests WHERE id = %s", (approval_id,)
    )
    payload = approval_rows[0]["payload"] if approval_rows else {}
    signal_data = payload.get("signal_data", {})

    positions = db.get_positions_by_pool(pool)
    total_positions_value = sum(
        float(p.get("quantity", 0)) * float(p.get("current_price", 0))
        for p in positions
    )

    cfg = _load_config()
    pools_cfg = _load_pools()
    pool_cfg = pools_cfg.get("pools", {}).get(pool, {})
    alloc_pct = pool_cfg.get("allocation_pct", 0)
    total_capital = cfg.get("investor_bot", {}).get("total_capital_usd", 10000)
    pool_capital = total_capital * alloc_pct / 100

    info_text = (
        f"<b>Extended Info — {symbol}</b>\n\n"
        f"Signal data:\n"
    )
    for k, v in signal_data.items():
        info_text += f"  {k}: {v}\n"

    info_text += (
        f"\n<b>Pool: {pool}</b>\n"
        f"Pool capital: ${pool_capital:.2f}\n"
        f"Positions value: ${total_positions_value:.2f}\n"
        f"Cash est.: ${pool_capital - total_positions_value:.2f}\n"
        f"Position count: {len(positions)}\n"
        f"\nStrategy: {pool_cfg.get('description', 'N/A')}\n"
        f"Time horizon: {pool_cfg.get('time_horizon', 'N/A')}\n"
    )

    await send_message(info_text)


async def _handle_trade_note_prompt(query: Any, approval_id: str) -> None:
    """Prompt user to send a note."""
    alert = db.get_trade_alert_by_approval(approval_id)
    if not alert:
        return
    user_id = query.from_user.id if query.from_user else 0
    _awaiting_note[user_id] = str(alert["id"])
    await send_message("Send your note:")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages (for notes)."""
    if not update.message or not update.message.from_user:
        return
    user_id = update.message.from_user.id
    if user_id in _awaiting_note:
        alert_id = _awaiting_note.pop(user_id)
        note_text = update.message.text or ""
        db.update_trade_alert_note(alert_id, note_text)
        await update.message.reply_text(f"Note saved on trade alert.")
        logger.info(f"Note saved on trade alert {alert_id}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Autonomous Ecosystem Bot\n\n"
        "Commands:\n"
        "/status — system status\n"
        "/portfolio — pool values\n"
        "/deals — recent deals\n"
        "/alerts — pending approvals\n"
        "/pause — pause all\n"
        "/resume — resume all\n"
        "/pause_deals — pause deals only\n"
        "/pause_investor — pause investor only\n"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _load_config()
    ds_enabled = cfg.get("mode", {}).get("deal_scout_phase2_enabled", False)
    ib_enabled = cfg.get("mode", {}).get("investor_bot_enabled", False)

    ds_status = "PAUSED" if _paused_deals else ("RUNNING" if ds_enabled else "DISABLED")
    ib_status = "PAUSED" if _paused_investor else ("RUNNING" if ib_enabled else "DISABLED")

    pending = db.get_pending_approvals()

    text = (
        f"<b>System Status</b>\n\n"
        f"Deal Scout Phase 2: <b>{ds_status}</b>\n"
        f"Investor Bot: <b>{ib_status}</b>\n"
        f"Paper Trading: <b>{'ON' if cfg.get('mode', {}).get('paper_trading', True) else 'OFF'}</b>\n"
        f"Pending approvals: <b>{len(pending)}</b>\n"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _load_config()
    pools_cfg = _load_pools()
    total_capital = cfg.get("investor_bot", {}).get("total_capital_usd", 10000)

    text = "<b>Portfolio Overview</b>\n\n"

    for pool_key, pool_info in pools_cfg.get("pools", {}).items():
        alloc_pct = pool_info.get("allocation_pct", 0)
        pool_capital = total_capital * alloc_pct / 100
        positions = db.get_positions_by_pool(pool_key)

        positions_value = sum(
            float(p.get("quantity", 0)) * float(p.get("current_price", 0))
            for p in positions
        )
        cash = pool_capital - positions_value
        cash_pct = (cash / pool_capital * 100) if pool_capital > 0 else 100

        text += f"<b>{pool_info.get('name', pool_key)}</b>\n"
        text += f"  Total: ${pool_capital:.2f} | Cash: ${cash:.2f} ({cash_pct:.0f}%)\n"
        text += f"  Positions: {len(positions)}\n"

        for p in positions[:5]:
            qty = float(p["quantity"])
            cost = float(p["avg_cost"])
            cur = float(p["current_price"])
            pnl = (cur - cost) * qty
            pnl_pct = ((cur / cost) - 1) * 100 if cost > 0 else 0
            sign = "+" if pnl >= 0 else ""
            text += f"    {p['symbol']}: {qty:.4f} @ ${cost:.4f} → ${cur:.4f} ({sign}{pnl_pct:.1f}%)\n"

        text += "\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_deals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.execute_query(
        "SELECT * FROM approval_requests WHERE system = 'deal_scout' ORDER BY created_at DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("No recent deals.")
        return

    text = "<b>Last 10 Deals</b>\n\n"
    for r in rows:
        payload = r.get("payload", {})
        title = payload.get("title", "Unknown")[:40]
        score = payload.get("score", 0)
        status = r["status"]
        text += f"{'[' + status.upper() + ']'} {title} — Score: {score}%\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = db.get_pending_approvals()
    if not pending:
        await update.message.reply_text("No pending approvals.")
        return

    text = f"<b>Pending Approvals ({len(pending)})</b>\n\n"
    for r in pending:
        system = r["system"]
        payload = r.get("payload", {})
        created = r["created_at"]
        if system == "deal_scout":
            text += f"DEAL: {payload.get('title', 'Unknown')[:40]} — ${payload.get('price', 0):.2f}\n"
        else:
            text += f"TRADE: {payload.get('action', 'BUY')} {payload.get('symbol', '???')} — Pool: {payload.get('pool', '?')}\n"
        text += f"  Created: {created}\n\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused_deals, _paused_investor
    _paused_deals = True
    _paused_investor = True
    await update.message.reply_text("Both systems paused. /resume to restart.")
    logger.info("Both systems paused via Telegram")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused_deals, _paused_investor
    _paused_deals = False
    _paused_investor = False
    await update.message.reply_text("Both systems resumed.")
    logger.info("Both systems resumed via Telegram")


async def cmd_pause_deals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused_deals
    _paused_deals = True
    await update.message.reply_text("Deal Scout paused. /resume to restart.")
    logger.info("Deal Scout paused via Telegram")


async def cmd_pause_investor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused_investor
    _paused_investor = True
    await update.message.reply_text("Investor Bot paused. /resume to restart.")
    logger.info("Investor Bot paused via Telegram")


# ---------------------------------------------------------------------------
# Approval expiry loop
# ---------------------------------------------------------------------------

async def expire_old_approvals() -> None:
    """Mark approvals older than 4 hours as expired and notify."""
    rows = db.execute_query(
        """
        SELECT * FROM approval_requests
        WHERE status = 'pending'
          AND created_at < NOW() - INTERVAL '4 hours'
        """
    )
    for row in rows:
        db.update_approval_status(row["id"], "expired")
        if row.get("telegram_msg_id"):
            await edit_message_buttons(
                row["telegram_msg_id"],
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("EXPIRED", callback_data="noop")]
                ]),
            )
            await send_message("Approval expired.")
        logger.info(f"Approval {row['id']} expired")

        # Also expire any associated trade alert
        alert = db.get_trade_alert_by_approval(str(row["id"]))
        if alert:
            db.update_trade_alert_status(alert["id"], "expired")


async def send_startup_message() -> None:
    """Send a startup confirmation to Telegram."""
    await send_message(
        "<b>Autonomous Ecosystem Online</b>\n\n"
        "Deal Scout Phase 2 + Investor Bot started.\n"
        "Use /status to check system health.\n"
        "Use /help for commands."
    )
