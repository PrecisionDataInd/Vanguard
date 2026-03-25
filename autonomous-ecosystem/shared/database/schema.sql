-- ============================================================================
-- Autonomous Ecosystem — PostgreSQL Schema
-- ============================================================================

-- Approval requests (shared by deal_scout and investor_bot)
CREATE TABLE IF NOT EXISTS approval_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system TEXT NOT NULL,             -- 'deal_scout' or 'investor_bot'
    type TEXT NOT NULL,               -- 'purchase' or 'trade'
    payload JSONB NOT NULL,           -- full deal or trade details
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected/expired
    created_at TIMESTAMPTZ DEFAULT NOW(),
    responded_at TIMESTAMPTZ,
    telegram_msg_id BIGINT            -- for button state updates
);

-- Purchases (deal scout phase 2)
CREATE TABLE IF NOT EXISTS purchases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_id UUID REFERENCES approval_requests(id),
    listing_id TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    title TEXT NOT NULL,
    asking_price NUMERIC(10,2),
    card_amount NUMERIC(10,2),
    order_number TEXT,
    status TEXT NOT NULL,              -- executing/completed/failed/cancelled
    failure_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Trade alerts (investor bot)
CREATE TABLE IF NOT EXISTS trade_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_id UUID REFERENCES approval_requests(id),
    pool TEXT NOT NULL,               -- aggressive/balanced/steady
    action TEXT NOT NULL,             -- BUY or SELL
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL,          -- stock/etf/crypto
    quantity NUMERIC(18,8),
    price_at_signal NUMERIC(18,8),
    notional_usd NUMERIC(10,2),
    signal_type TEXT NOT NULL,
    score NUMERIC(4,3),
    rationale TEXT,
    note TEXT,                        -- user-provided note via Telegram
    status TEXT NOT NULL,             -- pending/approved/rejected/expired
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Positions (paper trading)
CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL,          -- stock/etf/crypto
    quantity NUMERIC(18,8),
    avg_cost NUMERIC(18,8),
    current_price NUMERIC(18,8),
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pool, symbol)
);

-- Pool snapshots (portfolio tracking)
CREATE TABLE IF NOT EXISTS pool_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool TEXT NOT NULL,
    total_value_usd NUMERIC(12,2),
    cash_usd NUMERIC(12,2),
    positions_usd NUMERIC(12,2),
    snapshot_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_approval_requests_system ON approval_requests(system);
CREATE INDEX IF NOT EXISTS idx_trade_alerts_pool ON trade_alerts(pool);
CREATE INDEX IF NOT EXISTS idx_trade_alerts_status ON trade_alerts(status);
CREATE INDEX IF NOT EXISTS idx_positions_pool ON positions(pool);
