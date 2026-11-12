"""AgentCard MCP integration for virtual card issuance."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# MCP Client wrapper
# ---------------------------------------------------------------------------

_mcp_available: bool = False


async def _get_mcp_client():
    """Attempt to connect to the AgentCard MCP server.

    Returns a client session or None if unavailable.
    """
    global _mcp_available
    cfg = _load_config()
    mcp_url = cfg["api_keys"].get("agentcard_mcp_url", "")
    api_key = cfg["api_keys"].get("agentcard_api_key", "")

    if not mcp_url or mcp_url.startswith("YOUR_"):
        logger.warning("AgentCard MCP URL not configured")
        _mcp_available = False
        return None

    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        transport = sse_client(url=mcp_url, headers={"Authorization": f"Bearer {api_key}"})
        read_stream, write_stream = await transport.__aenter__()
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()
        _mcp_available = True
        return session
    except ImportError:
        logger.error("MCP SDK not installed. Run: pip install mcp")
        _mcp_available = False
        return None
    except Exception as exc:
        logger.error(f"Failed to connect to AgentCard MCP: {exc}")
        _mcp_available = False
        return None


def is_available() -> bool:
    """Check if AgentCard MCP is available."""
    return _mcp_available


async def check_availability() -> bool:
    """Test connection to AgentCard MCP and update availability status."""
    global _mcp_available
    session = None
    try:
        session = await _get_mcp_client()
        if session:
            _mcp_available = True
            logger.info("AgentCard MCP is available")
            return True
        return False
    except Exception as exc:
        logger.error(f"AgentCard availability check failed: {exc}")
        _mcp_available = False
        return False
    finally:
        if session:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Card operations
# ---------------------------------------------------------------------------

async def create_card(amount_usd: float, description: str) -> dict:
    """Issue a single-use virtual Mastercard via AgentCard MCP.

    Args:
        amount_usd: Card limit in USD.
        description: Description for the card (e.g. listing title).

    Returns:
        dict with keys: card_id, pan, cvv, expiry, status
    """
    session = None
    for attempt in range(3):
        try:
            session = await _get_mcp_client()
            if not session:
                raise ConnectionError("AgentCard MCP unavailable")

            result = await session.call_tool(
                "create_card",
                arguments={
                    "amount_usd": amount_usd,
                    "currency": "USD",
                    "type": "single_use",
                    "network": "mastercard",
                    "description": description[:200],
                },
            )

            if hasattr(result, "content") and result.content:
                import json
                data = json.loads(result.content[0].text)
                logger.info(f"Card created: {data.get('card_id', 'unknown')} for ${amount_usd:.2f}")
                return {
                    "card_id": data["card_id"],
                    "pan": data["pan"],
                    "cvv": data["cvv"],
                    "expiry": data["expiry"],
                    "status": data.get("status", "active"),
                }

            raise ValueError("Empty response from AgentCard")

        except Exception as exc:
            logger.warning(f"create_card attempt {attempt + 1}/3 failed: {exc}")
            if attempt < 2:
                await asyncio.sleep(2 ** (attempt + 1))
            else:
                raise
        finally:
            if session:
                try:
                    await session.__aexit__(None, None, None)
                except Exception:
                    pass
                session = None

    raise RuntimeError("create_card exhausted retries")


async def get_balance(card_id: str) -> float:
    """Get remaining balance on a card.

    Returns:
        Remaining balance in USD.
    """
    session = None
    try:
        session = await _get_mcp_client()
        if not session:
            raise ConnectionError("AgentCard MCP unavailable")

        result = await session.call_tool(
            "get_card_balance",
            arguments={"card_id": card_id},
        )

        if hasattr(result, "content") and result.content:
            import json
            data = json.loads(result.content[0].text)
            return float(data.get("balance_usd", 0))

        return 0.0
    except Exception as exc:
        logger.error(f"get_balance failed for {card_id}: {exc}")
        raise
    finally:
        if session:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass


async def cancel_card(card_id: str) -> bool:
    """Cancel/deactivate a virtual card.

    Returns:
        True on success.
    """
    session = None
    try:
        session = await _get_mcp_client()
        if not session:
            logger.warning(f"Cannot cancel card {card_id} — MCP unavailable")
            return False

        result = await session.call_tool(
            "cancel_card",
            arguments={"card_id": card_id},
        )

        logger.info(f"Card {card_id} cancelled")
        return True
    except Exception as exc:
        logger.error(f"cancel_card failed for {card_id}: {exc}")
        return False
    finally:
        if session:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass


async def list_active_cards() -> list[dict]:
    """List all active virtual cards.

    Returns:
        List of card dicts with card_id, amount, status, created_at.
    """
    session = None
    try:
        session = await _get_mcp_client()
        if not session:
            return []

        result = await session.call_tool(
            "list_cards",
            arguments={"status": "active"},
        )

        if hasattr(result, "content") and result.content:
            import json
            data = json.loads(result.content[0].text)
            return data.get("cards", [])

        return []
    except Exception as exc:
        logger.error(f"list_active_cards failed: {exc}")
        return []
    finally:
        if session:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
