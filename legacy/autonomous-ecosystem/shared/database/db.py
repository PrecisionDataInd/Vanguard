"""PostgreSQL connection pool and query helpers for the autonomous ecosystem."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool
import redis
import yaml
from loguru import logger
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Connection pools (module-level singletons, initialized on first call)
# ---------------------------------------------------------------------------

_pg_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_redis_client: redis.Redis | None = None


def init_db() -> None:
    """Initialize the PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is not None:
        return
    cfg = _load_config()
    dsn = cfg["database"]["postgres_url"]
    try:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=15,
            dsn=dsn,
        )
        logger.info("PostgreSQL connection pool initialized")
    except psycopg2.Error as exc:
        logger.error(f"Failed to initialize PostgreSQL pool: {exc}")
        raise


def init_redis() -> None:
    """Initialize the Redis client."""
    global _redis_client
    if _redis_client is not None:
        return
    cfg = _load_config()
    url = cfg["database"]["redis_url"]
    try:
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        logger.info("Redis connection established")
    except redis.RedisError as exc:
        logger.error(f"Failed to connect to Redis: {exc}")
        raise


def get_redis() -> redis.Redis:
    """Return the Redis client, initializing if needed."""
    if _redis_client is None:
        init_redis()
    return _redis_client  # type: ignore[return-value]


@contextmanager
def _get_conn():
    """Context manager that checks out and returns a connection to the pool."""
    if _pg_pool is None:
        init_db()
    conn = _pg_pool.getconn()  # type: ignore[union-attr]
    try:
        yield conn
    finally:
        _pg_pool.putconn(conn)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Generic query helpers
# ---------------------------------------------------------------------------

psycopg2.extras.register_uuid()


def execute_query(sql: str, params: tuple | None = None) -> list[dict]:
    """Execute a read query and return rows as list of dicts."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def execute_write(sql: str, params: tuple | None = None) -> int:
    """Execute a write query with auto-commit. Returns rowcount."""
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
                return cur.rowcount
        except psycopg2.Error:
            conn.rollback()
            raise


def execute_write_returning(sql: str, params: tuple | None = None) -> dict | None:
    """Execute a write query that returns a row (INSERT ... RETURNING *)."""
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                conn.commit()
                row = cur.fetchone()
                return dict(row) if row else None
        except psycopg2.Error:
            conn.rollback()
            raise


# ---------------------------------------------------------------------------
# Domain-specific functions
# ---------------------------------------------------------------------------

def insert_approval_request(
    system: str,
    req_type: str,
    payload: dict,
    telegram_msg_id: int | None = None,
) -> dict:
    """Insert a new approval request and return the full row."""
    sql = """
        INSERT INTO approval_requests (system, type, payload, status, telegram_msg_id)
        VALUES (%s, %s, %s, 'pending', %s)
        RETURNING *
    """
    row = execute_write_returning(
        sql,
        (system, req_type, psycopg2.extras.Json(payload), telegram_msg_id),
    )
    if row:
        logger.info(f"Created approval request {row['id']} for {system}/{req_type}")

        # Cache in Redis with 4h TTL
        try:
            r = get_redis()
            r.setex(f"approval:{row['id']}", 4 * 3600, "pending")
        except Exception as exc:
            logger.warning(f"Redis cache failed for approval {row['id']}: {exc}")

    return row  # type: ignore[return-value]


def update_approval_status(
    approval_id: uuid.UUID | str,
    status: str,
    telegram_msg_id: int | None = None,
) -> int:
    """Update approval request status."""
    parts = ["status = %s", "responded_at = %s"]
    params: list[Any] = [status, datetime.now(timezone.utc)]

    if telegram_msg_id is not None:
        parts.append("telegram_msg_id = %s")
        params.append(telegram_msg_id)

    params.append(str(approval_id))
    sql = f"UPDATE approval_requests SET {', '.join(parts)} WHERE id = %s"
    count = execute_write(sql, tuple(params))
    if count:
        logger.info(f"Approval {approval_id} -> {status}")
        try:
            r = get_redis()
            r.setex(f"approval:{approval_id}", 4 * 3600, status)
        except Exception:
            pass
    return count


def insert_purchase(
    approval_id: uuid.UUID | str,
    listing_id: str,
    marketplace: str,
    title: str,
    asking_price: float,
    card_amount: float,
    status: str = "executing",
) -> dict:
    """Insert a purchase record."""
    sql = """
        INSERT INTO purchases
            (approval_id, listing_id, marketplace, title, asking_price, card_amount, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    """
    row = execute_write_returning(
        sql,
        (str(approval_id), listing_id, marketplace, title, asking_price, card_amount, status),
    )
    logger.info(f"Purchase record created: {row['id'] if row else 'FAILED'}")
    return row  # type: ignore[return-value]


def update_purchase_status(
    purchase_id: uuid.UUID | str,
    status: str,
    order_number: str | None = None,
    failure_reason: str | None = None,
) -> int:
    """Update a purchase record status."""
    parts = ["status = %s"]
    params: list[Any] = [status]
    if order_number:
        parts.append("order_number = %s")
        params.append(order_number)
    if failure_reason:
        parts.append("failure_reason = %s")
        params.append(failure_reason)
    if status in ("completed", "failed", "cancelled"):
        parts.append("completed_at = %s")
        params.append(datetime.now(timezone.utc))
    params.append(str(purchase_id))
    sql = f"UPDATE purchases SET {', '.join(parts)} WHERE id = %s"
    return execute_write(sql, tuple(params))


def insert_trade_alert(
    approval_id: uuid.UUID | str,
    pool: str,
    action: str,
    symbol: str,
    asset_type: str,
    quantity: float,
    price_at_signal: float,
    notional_usd: float,
    signal_type: str,
    score: float,
    rationale: str,
    status: str = "pending",
) -> dict:
    """Insert a trade alert record."""
    sql = """
        INSERT INTO trade_alerts
            (approval_id, pool, action, symbol, asset_type, quantity,
             price_at_signal, notional_usd, signal_type, score, rationale, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    """
    row = execute_write_returning(
        sql,
        (
            str(approval_id), pool, action, symbol, asset_type, quantity,
            price_at_signal, notional_usd, signal_type, score, rationale, status,
        ),
    )
    logger.info(f"Trade alert created: {row['id'] if row else 'FAILED'}")
    return row  # type: ignore[return-value]


def update_trade_alert_status(alert_id: uuid.UUID | str, status: str) -> int:
    """Update trade alert status."""
    sql = "UPDATE trade_alerts SET status = %s WHERE id = %s"
    return execute_write(sql, (status, str(alert_id)))


def update_trade_alert_note(alert_id: uuid.UUID | str, note: str) -> int:
    """Store a user note on a trade alert."""
    sql = "UPDATE trade_alerts SET note = %s WHERE id = %s"
    return execute_write(sql, (note, str(alert_id)))


def upsert_position(
    pool: str,
    symbol: str,
    asset_type: str,
    quantity: float,
    avg_cost: float,
    current_price: float,
) -> dict:
    """Insert or update a position (paper trading)."""
    sql = """
        INSERT INTO positions (pool, symbol, asset_type, quantity, avg_cost, current_price, last_updated)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (pool, symbol) DO UPDATE SET
            quantity = positions.quantity + EXCLUDED.quantity,
            avg_cost = (
                (positions.quantity * positions.avg_cost + EXCLUDED.quantity * EXCLUDED.avg_cost)
                / NULLIF(positions.quantity + EXCLUDED.quantity, 0)
            ),
            current_price = EXCLUDED.current_price,
            last_updated = NOW()
        RETURNING *
    """
    row = execute_write_returning(
        sql,
        (pool, symbol, asset_type, quantity, avg_cost, current_price),
    )
    logger.info(f"Position upserted: {pool}/{symbol}")
    return row  # type: ignore[return-value]


def reduce_position(pool: str, symbol: str, sell_quantity: float) -> dict | None:
    """Reduce a position quantity. Deletes if quantity reaches zero."""
    pos = execute_query(
        "SELECT * FROM positions WHERE pool = %s AND symbol = %s", (pool, symbol)
    )
    if not pos:
        logger.warning(f"No position found for {pool}/{symbol}")
        return None

    remaining = float(pos[0]["quantity"]) - sell_quantity
    if remaining <= 0:
        execute_write(
            "DELETE FROM positions WHERE pool = %s AND symbol = %s", (pool, symbol)
        )
        logger.info(f"Position closed: {pool}/{symbol}")
        return {"pool": pool, "symbol": symbol, "quantity": 0, "closed": True}

    sql = "UPDATE positions SET quantity = %s, last_updated = NOW() WHERE pool = %s AND symbol = %s RETURNING *"
    row = execute_write_returning(sql, (remaining, pool, symbol))
    return row  # type: ignore[return-value]


def get_pending_approvals(system: str | None = None) -> list[dict]:
    """Get all pending approval requests, optionally filtered by system."""
    if system:
        return execute_query(
            "SELECT * FROM approval_requests WHERE status = 'pending' AND system = %s ORDER BY created_at DESC",
            (system,),
        )
    return execute_query(
        "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY created_at DESC"
    )


def get_positions_by_pool(pool: str) -> list[dict]:
    """Get all positions for a given pool."""
    return execute_query(
        "SELECT * FROM positions WHERE pool = %s ORDER BY symbol", (pool,)
    )


def get_all_positions() -> list[dict]:
    """Get all positions across all pools."""
    return execute_query("SELECT * FROM positions ORDER BY pool, symbol")


def insert_pool_snapshot(pool: str, total_value: float, cash: float, positions_value: float) -> dict:
    """Insert a pool value snapshot."""
    sql = """
        INSERT INTO pool_snapshots (pool, total_value_usd, cash_usd, positions_usd)
        VALUES (%s, %s, %s, %s) RETURNING *
    """
    row = execute_write_returning(sql, (pool, total_value, cash, positions_value))
    return row  # type: ignore[return-value]


def get_recent_trade_alerts(limit: int = 10) -> list[dict]:
    """Get most recent trade alerts."""
    return execute_query(
        "SELECT * FROM trade_alerts ORDER BY created_at DESC LIMIT %s", (limit,)
    )


def get_trade_alert_by_approval(approval_id: str) -> dict | None:
    """Get trade alert by its approval_id."""
    rows = execute_query(
        "SELECT * FROM trade_alerts WHERE approval_id = %s", (approval_id,)
    )
    return rows[0] if rows else None


def get_purchase_by_approval(approval_id: str) -> dict | None:
    """Get purchase by its approval_id."""
    rows = execute_query(
        "SELECT * FROM purchases WHERE approval_id = %s", (approval_id,)
    )
    return rows[0] if rows else None


def check_alert_cooldown(symbol: str, cooldown_hours: int = 6) -> bool:
    """Return True if symbol was alerted within cooldown period (should skip)."""
    sql = """
        SELECT COUNT(*) as cnt FROM trade_alerts
        WHERE symbol = %s AND created_at > NOW() - INTERVAL '%s hours'
    """
    rows = execute_query(sql, (symbol, cooldown_hours))
    return rows[0]["cnt"] > 0 if rows else False


def run_schema() -> None:
    """Execute schema.sql to create/update tables."""
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path) as f:
        sql = f.read()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    logger.info("Database schema applied successfully")
