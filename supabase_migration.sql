-- NEXUS Trading Dashboard — Supabase Migration
-- Run this in Supabase SQL Editor

-- Paper Account (single row)
CREATE TABLE IF NOT EXISTS paper_account (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  balance       NUMERIC(12,2) NOT NULL DEFAULT 25000.00,
  allocated     NUMERIC(12,2) NOT NULL DEFAULT 0.00,
  free          NUMERIC(12,2) NOT NULL DEFAULT 25000.00,
  today_pnl     NUMERIC(12,2) NOT NULL DEFAULT 0.00,
  total_pnl     NUMERIC(12,2) NOT NULL DEFAULT 0.00,
  total_trades  INT NOT NULL DEFAULT 0,
  win_rate      NUMERIC(5,2) NOT NULL DEFAULT 0.00,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Paper Positions
CREATE TABLE IF NOT EXISTS paper_positions (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  signal_id           TEXT,
  ticker              TEXT NOT NULL,
  direction           TEXT NOT NULL,          -- LONG / SHORT
  setup_family        TEXT,
  grade               TEXT,                   -- A+, A, B, C
  confidence          NUMERIC(5,2),
  market_state        TEXT,
  entry_time          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  entry_stock_price   NUMERIC(12,4),
  entry_premium       NUMERIC(12,4),
  contracts           INT NOT NULL DEFAULT 1,
  cost_basis          NUMERIC(12,2),
  current_premium     NUMERIC(12,4),
  current_stock_price NUMERIC(12,4),
  current_pnl         NUMERIC(12,2),
  tp1_premium         NUMERIC(12,4),
  tp2_premium         NUMERIC(12,4),
  tp3_premium         NUMERIC(12,4),
  sl_premium          NUMERIC(12,4),
  tp1_stock           NUMERIC(12,4),
  tp2_stock           NUMERIC(12,4),
  tp3_stock           NUMERIC(12,4),
  sl_stock            NUMERIC(12,4),
  tp1_hit             BOOLEAN NOT NULL DEFAULT FALSE,
  tp2_hit             BOOLEAN NOT NULL DEFAULT FALSE,
  tp3_hit             BOOLEAN NOT NULL DEFAULT FALSE,
  stop_at_breakeven   BOOLEAN NOT NULL DEFAULT FALSE,
  status              TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN / CLOSING / CLOSED
  agent_note          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Paper Trades (closed)
CREATE TABLE IF NOT EXISTS paper_trades (
  id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  signal_id               TEXT,
  ticker                  TEXT NOT NULL,
  direction               TEXT NOT NULL,
  setup_family            TEXT,
  grade                   TEXT,
  confidence              NUMERIC(5,2),
  market_state            TEXT,
  entry_time              TIMESTAMPTZ,
  exit_time               TIMESTAMPTZ,
  time_in_trade_minutes   INT,
  entry_premium           NUMERIC(12,4),
  exit_premium            NUMERIC(12,4),
  entry_stock_price       NUMERIC(12,4),
  exit_stock_price        NUMERIC(12,4),
  contracts               INT NOT NULL DEFAULT 1,
  cost_basis              NUMERIC(12,2),
  proceeds                NUMERIC(12,2),
  pnl_dollars             NUMERIC(12,2),
  pnl_pct                 NUMERIC(8,2),
  exit_reason             TEXT,               -- TP1/TP2/TP3/SL/TIME/ROTATED/MANUAL
  tp1_hit                 BOOLEAN NOT NULL DEFAULT FALSE,
  tp2_hit                 BOOLEAN NOT NULL DEFAULT FALSE,
  tp3_hit                 BOOLEAN NOT NULL DEFAULT FALSE,
  max_favorable_excursion NUMERIC(12,4),
  max_adverse_excursion   NUMERIC(12,4),
  signal_date             DATE,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agent Control (single row)
CREATE TABLE IF NOT EXISTS agent_control (
  id          INT PRIMARY KEY DEFAULT 1,
  status      TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING / PAUSED / EMERGENCY_CLOSE
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by  TEXT
);

-- Default rows
INSERT INTO paper_account (balance, allocated, free)
VALUES (25000.00, 0.00, 25000.00)
ON CONFLICT DO NOTHING;

INSERT INTO agent_control (id, status)
VALUES (1, 'RUNNING')
ON CONFLICT (id) DO NOTHING;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON paper_trades(exit_time);
CREATE INDEX IF NOT EXISTS idx_trades_signal_date ON paper_trades(signal_date);
