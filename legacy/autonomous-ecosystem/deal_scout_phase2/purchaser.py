"""Autonomous purchase state machine for Deal Scout Phase 2.

Polls for approved deals and executes the purchase flow:
PENDING_APPROVAL -> CARD_ISSUANCE -> BROWSER_LAUNCH -> CHECKOUT -> COMPLETED/FAILED
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from deal_scout_phase2 import agentcard, browserbase_session
from shared.alerts import telegram
from shared.database import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_purchaser_available: bool = False
_processed_listings: set[str] = set()  # listing_ids we've already handled

# Daily spend tracking (reset at midnight UTC via Redis)
DAILY_SPEND_KEY = "deal_scout:daily_spend"


def _get_daily_spend() -> float:
    try:
        r = db.get_redis()
        val = r.get(DAILY_SPEND_KEY)
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def _add_daily_spend(amount: float) -> None:
    try:
        r = db.get_redis()
        current = float(r.get(DAILY_SPEND_KEY) or 0)
        pipe = r.pipeline()
        pipe.set(DAILY_SPEND_KEY, current + amount)
        # Expire at next midnight UTC
        import time
        now = time.time()
        seconds_until_midnight = 86400 - (int(now) % 86400)
        pipe.expire(DAILY_SPEND_KEY, seconds_until_midnight)
        pipe.execute()
    except Exception as exc:
        logger.error(f"Failed to update daily spend: {exc}")


# ---------------------------------------------------------------------------
# OpenClaw checkout prompt
# ---------------------------------------------------------------------------

def _build_checkout_prompt(card_info: dict, deal: dict, config: dict) -> str:
    """Build the system prompt for the OpenClaw checkout agent."""
    addr = config.get("shipping_address", {})
    card_amount = card_info.get("amount_usd", 0)

    return (
        "You are a purchasing agent. Your only job is to complete one checkout.\n"
        "The browser is already open on the listing page.\n\n"
        f"You have a virtual Mastercard loaded with exactly ${card_amount:.2f}.\n\n"
        f"Card number: {card_info['pan']}\n"
        f"CVV: {card_info['cvv']}\n"
        f"Expiry: {card_info['expiry']}\n\n"
        f"Shipping address:\n"
        f"{addr.get('name', '')}\n"
        f"{addr.get('address1', '')}\n"
        f"{addr.get('address2', '')}\n"
        f"{addr.get('city', '')}, {addr.get('state', '')} {addr.get('zip', '')}\n"
        f"Phone: {addr.get('phone', '')}\n\n"
        "Instructions:\n"
        "1. Add item to cart if not already added\n"
        "2. Go to checkout\n"
        "3. Select guest checkout — do NOT create an account\n"
        "4. Enter the shipping address above\n"
        "5. Enter the card details above\n"
        f"6. Review the total — if it exceeds ${card_amount:.2f}, stop and report back\n"
        "7. Submit the order\n"
        "8. Return ONLY the order confirmation number\n\n"
        "Hard rules:\n"
        "* No upsells, warranties, or add-ons\n"
        f"* If total exceeds card amount by more than $2, ABORT\n"
        "* If CAPTCHA appears, ABORT and report 'captcha_encountered'\n"
        "* If checkout fails twice, ABORT and report 'checkout_failed'\n"
        "* Do not save payment info or create accounts"
    )


# ---------------------------------------------------------------------------
# Purchase flow
# ---------------------------------------------------------------------------

async def _execute_purchase(approval_row: dict) -> None:
    """Execute the full purchase flow for an approved deal."""
    config = _load_config()
    payload = approval_row["payload"]
    approval_id = str(approval_row["id"])
    listing_id = payload.get("listing_id", "")
    marketplace = payload.get("marketplace", "unknown")
    title = payload.get("title", "Unknown Item")
    asking_price = float(payload.get("price", 0))
    listing_url = payload.get("listing_url", "")

    # --- Safety checks ---
    max_purchase = config["deal_scout"].get("max_purchase_usd", 500)
    buffer_pct = config["deal_scout"].get("shipping_buffer_pct", 0.15)
    card_amount = asking_price * (1 + buffer_pct)
    max_daily = config["deal_scout"].get("max_daily_spend_usd", 1000)

    assert card_amount <= max_purchase * (1 + buffer_pct), \
        f"Card cap: ${max_purchase} + {buffer_pct*100:.0f}% = ${max_purchase * (1 + buffer_pct):.2f} max"

    current_daily = _get_daily_spend()
    assert current_daily + asking_price <= max_daily, \
        f"Daily deal spend cap: ${max_daily:.2f}"

    if not config["mode"].get("deal_scout_phase2_enabled", False):
        logger.warning("Deal Scout Phase 2 disabled in config, skipping purchase")
        return

    # Check Telegram is reachable
    if telegram.get_app() is None:
        logger.error("Telegram bot unreachable — cannot proceed with purchase")
        await telegram.send_message(f"Cannot purchase: Telegram bot not connected")
        return

    # Create purchase record
    purchase = db.insert_purchase(
        approval_id=approval_id,
        listing_id=listing_id,
        marketplace=marketplace,
        title=title,
        asking_price=asking_price,
        card_amount=card_amount,
        status="executing",
    )
    purchase_id = str(purchase["id"])

    # --- CARD_ISSUANCE ---
    logger.info(f"[{purchase_id}] CARD_ISSUANCE: Requesting ${card_amount:.2f} card")
    card_info = None
    try:
        card_info = await agentcard.create_card(
            amount_usd=card_amount,
            description=f"Deal Scout: {title[:100]}",
        )
        card_info["amount_usd"] = card_amount
        logger.info(f"[{purchase_id}] Card issued: {card_info['card_id']}")
    except Exception as exc:
        logger.error(f"[{purchase_id}] AgentCard failed: {exc}")
        db.update_purchase_status(purchase_id, "failed", failure_reason=f"AgentCard failed: {exc}")
        await telegram.send_message(f"AgentCard failed for '{title[:40]}': {exc}")
        return

    # --- BROWSER_LAUNCH ---
    logger.info(f"[{purchase_id}] BROWSER_LAUNCH: Opening {listing_url}")
    session_info = None
    try:
        session_info = await browserbase_session.create_session(listing_url)
        logger.info(f"[{purchase_id}] Browser session: {session_info['session_id']}")
    except Exception as exc:
        logger.error(f"[{purchase_id}] Browser launch failed: {exc}")
        await agentcard.cancel_card(card_info["card_id"])
        db.update_purchase_status(purchase_id, "failed", failure_reason=f"Browser launch failed: {exc}")
        await telegram.send_message(f"Browser launch failed for '{title[:40]}': {exc}")
        return

    # --- CHECKOUT ---
    logger.info(f"[{purchase_id}] CHECKOUT: Handing to OpenClaw agent")
    try:
        checkout_prompt = _build_checkout_prompt(card_info, payload, config)
        order_number = await _run_checkout_agent(
            session_info=session_info,
            checkout_prompt=checkout_prompt,
            config=config,
            timeout_minutes=10,
        )

        if order_number:
            # --- COMPLETED ---
            logger.info(f"[{purchase_id}] COMPLETED: Order #{order_number}")
            db.update_purchase_status(purchase_id, "completed", order_number=order_number)
            _add_daily_spend(asking_price)
            await telegram.send_message(
                f"Purchased! Order #{order_number} confirmed.\n"
                f"'{title[:50]}' from {marketplace} for ${asking_price:.2f}"
            )
        else:
            raise RuntimeError("No order number returned from checkout agent")

    except Exception as exc:
        logger.error(f"[{purchase_id}] Checkout failed: {exc}")
        await agentcard.cancel_card(card_info["card_id"])
        db.update_purchase_status(purchase_id, "failed", failure_reason=f"Checkout failed: {exc}")
        await telegram.send_message(
            f"Purchase failed: {exc}\n"
            f"'{title[:50]}' — Deal still live."
        )
    finally:
        # Always close browser session
        if session_info:
            await browserbase_session.close_session(session_info["session_id"])


async def _run_checkout_agent(
    session_info: dict,
    checkout_prompt: str,
    config: dict,
    timeout_minutes: int = 10,
) -> str | None:
    """Run OpenClaw agent to complete checkout.

    Returns order confirmation number or None.
    """
    try:
        import anthropic

        api_key = config["api_keys"].get("anthropic_api_key", "")
        if not api_key or api_key.startswith("YOUR_"):
            raise ValueError("Anthropic API key not configured")

        client = anthropic.Anthropic(api_key=api_key)

        # Use computer-use style interaction with the browser
        connect_url = session_info.get("connect_url", "")

        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.messages.create,
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=checkout_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"The browser is connected at: {connect_url}\n"
                            "Please complete the checkout now. "
                            "Return ONLY the order confirmation number when done."
                        ),
                    }
                ],
            ),
            timeout=timeout_minutes * 60,
        )

        # Extract order number from response
        if response.content:
            text = response.content[0].text.strip()
            logger.info(f"Checkout agent response: {text}")
            # The agent should return just the order number
            # Clean up any extra text
            for line in text.split("\n"):
                line = line.strip()
                if line and not line.startswith("I") and not line.startswith("The"):
                    return line
            return text[:100] if text else None

        return None

    except asyncio.TimeoutError:
        logger.error("Checkout agent timed out")
        raise RuntimeError("Checkout timed out after 10 minutes")
    except Exception as exc:
        logger.error(f"Checkout agent error: {exc}")
        raise


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

async def init_purchaser() -> bool:
    """Initialize the purchaser and check dependencies.

    Returns True if purchasing is available.
    """
    global _purchaser_available

    config = _load_config()
    if not config["mode"].get("deal_scout_phase2_enabled", False):
        logger.info("Deal Scout Phase 2 disabled in config")
        _purchaser_available = False
        return False

    # Check AgentCard availability
    agentcard_ok = await agentcard.check_availability()
    if not agentcard_ok:
        logger.warning("AgentCard unavailable. Purchase capability disabled.")
        await telegram.send_message(
            "AgentCard unavailable. Purchase capability disabled.\n"
            "Deal Scout continues alerting as normal."
        )
        _purchaser_available = False
        return False

    _purchaser_available = True
    logger.info("Purchaser initialized and ready")
    return True


async def purchaser_loop() -> None:
    """Main loop: polls for approved deals every 30 seconds and executes purchases."""
    logger.info("Purchaser loop started")

    while True:
        try:
            if telegram.is_deals_paused():
                await asyncio.sleep(60)
                continue

            if not _purchaser_available:
                await asyncio.sleep(120)
                continue

            # Check for approved deal requests
            approved = db.execute_query(
                """
                SELECT * FROM approval_requests
                WHERE system = 'deal_scout'
                  AND type = 'purchase'
                  AND status = 'approved'
                ORDER BY created_at ASC
                """
            )

            for row in approved:
                listing_id = row["payload"].get("listing_id", "")

                # Skip already-processed listings
                if listing_id in _processed_listings:
                    continue

                # Check if purchase already exists for this approval
                existing = db.get_purchase_by_approval(str(row["id"]))
                if existing:
                    _processed_listings.add(listing_id)
                    continue

                _processed_listings.add(listing_id)

                try:
                    await _execute_purchase(row)
                except AssertionError as exc:
                    logger.error(f"Safety check failed: {exc}")
                    await telegram.send_message(f"Purchase blocked by safety check: {exc}")
                    db.update_approval_status(row["id"], "rejected")
                except Exception as exc:
                    logger.error(f"Purchase execution error: {exc}")

            # Also handle rejected deals — mark listing as processed
            rejected = db.execute_query(
                """
                SELECT * FROM approval_requests
                WHERE system = 'deal_scout'
                  AND type = 'purchase'
                  AND status IN ('rejected', 'expired')
                  AND responded_at > NOW() - INTERVAL '1 hour'
                """
            )
            for row in rejected:
                listing_id = row["payload"].get("listing_id", "")
                _processed_listings.add(listing_id)

        except Exception as exc:
            logger.error(f"Purchaser loop error: {exc}")

        await asyncio.sleep(30)
