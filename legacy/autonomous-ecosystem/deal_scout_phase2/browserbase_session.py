"""Browserbase browser session manager for autonomous checkout."""

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

# Max 1 concurrent session
_active_session_id: str | None = None
_session_lock = asyncio.Lock()


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _get_api_key() -> str:
    cfg = _load_config()
    key = cfg["api_keys"].get("browserbase_api_key", "")
    if not key or key.startswith("YOUR_"):
        raise ValueError("Browserbase API key not configured")
    return key


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def create_session(url: str) -> dict:
    """Create a stealth browser session and navigate to the given URL.

    Args:
        url: The URL to navigate to after session creation.

    Returns:
        dict with keys: session_id, live_url, connect_url

    Raises:
        TimeoutError: If session creation takes longer than 30 seconds.
        RuntimeError: If another session is already active.
    """
    global _active_session_id

    async with _session_lock:
        if _active_session_id is not None:
            raise RuntimeError(
                f"Another session is already active: {_active_session_id}. "
                "Close it first (max 1 concurrent session)."
            )

    api_key = _get_api_key()

    for attempt in range(3):
        try:
            result = await asyncio.wait_for(
                _create_session_impl(api_key, url),
                timeout=30.0,
            )
            async with _session_lock:
                _active_session_id = result["session_id"]
            logger.info(f"Browser session created: {result['session_id']} -> {url}")
            return result

        except asyncio.TimeoutError:
            logger.error(f"Session creation timed out (attempt {attempt + 1}/3)")
            if attempt < 2:
                await asyncio.sleep(2 ** (attempt + 1))
            else:
                raise TimeoutError("Browser session creation timed out after 3 attempts")

        except Exception as exc:
            logger.error(f"Session creation failed (attempt {attempt + 1}/3): {exc}")
            if attempt < 2:
                await asyncio.sleep(2 ** (attempt + 1))
            else:
                raise

    raise RuntimeError("create_session exhausted retries")


async def _create_session_impl(api_key: str, url: str) -> dict:
    """Internal session creation using Browserbase SDK."""
    try:
        from browserbase import Browserbase

        bb = Browserbase(api_key=api_key)
        session = bb.sessions.create(
            project_id=bb.list_projects()[0].id if hasattr(bb, "list_projects") else None,
        )

        session_id = session.id if hasattr(session, "id") else str(session)

        connect_url = bb.sessions.debug(session_id).debugger_fullscreen_url
        live_url = connect_url

        return {
            "session_id": session_id,
            "live_url": live_url,
            "connect_url": connect_url,
        }

    except ImportError:
        logger.error("Browserbase SDK not installed. Run: pip install browserbase")
        raise
    except Exception as exc:
        logger.error(f"Browserbase session creation error: {exc}")
        raise


async def close_session(session_id: str) -> bool:
    """Close and destroy a browser session.

    Args:
        session_id: The session to close.

    Returns:
        True on success.
    """
    global _active_session_id

    try:
        api_key = _get_api_key()
        from browserbase import Browserbase

        bb = Browserbase(api_key=api_key)
        bb.sessions.update(session_id, status="REQUEST_RELEASE")

        async with _session_lock:
            if _active_session_id == session_id:
                _active_session_id = None

        logger.info(f"Browser session closed: {session_id}")
        return True

    except Exception as exc:
        logger.error(f"Failed to close session {session_id}: {exc}")
        # Still clear the active session to prevent deadlocks
        async with _session_lock:
            if _active_session_id == session_id:
                _active_session_id = None
        return False


async def screenshot(session_id: str) -> bytes:
    """Take a PNG screenshot of the current browser state.

    Args:
        session_id: The session to screenshot.

    Returns:
        PNG image bytes.
    """
    try:
        api_key = _get_api_key()

        # Use Playwright to connect to the session and take a screenshot
        from playwright.async_api import async_playwright

        from browserbase import Browserbase

        bb = Browserbase(api_key=api_key)
        debug_info = bb.sessions.debug(session_id)
        ws_url = debug_info.ws_url if hasattr(debug_info, "ws_url") else str(debug_info)

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws_url)
            pages = browser.contexts[0].pages
            if pages:
                png_bytes = await pages[0].screenshot(type="png")
                logger.info(f"Screenshot taken for session {session_id}")
                return png_bytes
            raise RuntimeError("No pages found in browser session")

    except Exception as exc:
        logger.error(f"Screenshot failed for session {session_id}: {exc}")
        raise


def get_active_session() -> str | None:
    """Return the currently active session ID, or None."""
    return _active_session_id
