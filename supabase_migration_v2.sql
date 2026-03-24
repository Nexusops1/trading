-- NEXUS LAB MODE — Paper Trading Engine Schema v2
-- Run this in Supabase SQL Editor
-- This replaces the mock-data tables with the full LAB MODE schema.

-- ══════════════════════════════════════════════════════════════════
-- 1. Drop old mock tables (they held seed data only)
-- ══════════════════════════════════════════════════════════════════
DROP TABLE IF EXISTS paper_trades CASCADE;
DROP TABLE IF EXISTS paper_positions CASCADE;
DROP TABLE IF EXISTS paper_account CASCADE;
DROP TABLE IF EXISTS agent_control CASCADE;

-- ══════════════════════════════════════════════════════════════════
-- 2. paper_positions — one row per signal experiment
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE paper_positions (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id               TEXT NOT NULL,
  parent_scan_id          TEXT,
  ticker                  TEXT NOT NULL,
  underlying_ticker       TEXT,
  option_contract         TEXT,
  expiry                  DATE,
  strike                  NUMERIC,
  option_type             TEXT,            -- CALL / PUT
  direction               TEXT NOT NULL,   -- long / short / bearish / bullish
  quantity                INTEGER NOT NULL DEFAULT 1,
  status                  TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN / CLOSED
  agent_state             TEXT NOT NULL DEFAULT 'MONITORING',  -- MONITORING / AT_BREAKEVEN / TP1_HIT / TP2_HIT

  -- Entry pricing
  entry_bid               NUMERIC,
  entry_ask               NUMERIC,
  entry_mid               NUMERIC,
  entry_price             NUMERIC NOT NULL,
  underlying_entry_price  NUMERIC,

  -- Live pricing (updated each cycle)
  current_price           NUMERIC,
  underlying_current_price NUMERIC,

  -- Levels
  stop_price              NUMERIC,
  original_stop_price     NUMERIC,
  tp1_price               NUMERIC,
  tp2_price               NUMERIC,
  tp3_price               NUMERIC,

  -- Timestamps
  entry_timestamp         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  exit_timestamp          TIMESTAMPTZ,
  exit_reason             TEXT,            -- TP3_HIT / STOP_LOSS / BREAKEVEN_STOP / TP1_STOPBACK / TP2_STOPBACK / TIME_STOP / END_OF_DAY_CLOSE / EMERGENCY_CLOSE / DATA_ERROR_CLOSE

  -- Outcome metrics (computed at close)
  realized_pnl_dollars    NUMERIC,
  realized_pnl_percent    NUMERIC,
  unrealized_pnl_dollars  NUMERIC,
  unrealized_pnl_percent  NUMERIC,
  max_favorable_excursion NUMERIC,
  max_adverse_excursion   NUMERIC,
  best_price_seen         NUMERIC,
  worst_price_seen        NUMERIC,
  time_in_trade_seconds   INTEGER,
  hit_tp1                 BOOLEAN NOT NULL DEFAULT FALSE,
  hit_tp2                 BOOLEAN NOT NULL DEFAULT FALSE,
  hit_tp3                 BOOLEAN NOT NULL DEFAULT FALSE,

  -- Signal clustering
  sequence_index_for_ticker                       INTEGER,
  minutes_since_last_signal_same_ticker            NUMERIC,
  minutes_since_last_signal_same_direction_same_ticker NUMERIC,
  sibling_signal_count_nearby                      INTEGER,
  signal_cluster_id       TEXT,

  -- Frozen signal snapshot
  grade                   TEXT,
  setup_family            TEXT,
  confidence              NUMERIC,
  score_total             NUMERIC,
  confluence_count        INTEGER,
  market_state            TEXT,
  gamma_context           TEXT,
  volatility_state        TEXT,
  vix_context             TEXT,
  raw_signal_json         JSONB,
  metadata_json           JSONB,

  -- Quote failure tracking
  consecutive_quote_failures INTEGER NOT NULL DEFAULT 0,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pp_status ON paper_positions(status);
CREATE INDEX idx_pp_ticker ON paper_positions(ticker);
CREATE INDEX idx_pp_signal_id ON paper_positions(signal_id);
CREATE INDEX idx_pp_entry_timestamp ON paper_positions(entry_timestamp);
CREATE INDEX idx_pp_created_at ON paper_positions(created_at);

-- ══════════════════════════════════════════════════════════════════
-- 3. paper_position_events — full lifecycle event log
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE paper_position_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  position_id         UUID NOT NULL REFERENCES paper_positions(id),
  signal_id           TEXT,
  event_type          TEXT NOT NULL,   -- ENTRY, TP1_HIT, TP2_HIT, STOP_MOVED, EXIT, EMERGENCY_EXIT, END_OF_DAY_EXIT, TIME_STOP_EXIT, REJECTED_ENTRY, QUOTE_MISSING
  timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  option_price        NUMERIC,
  underlying_price    NUMERIC,
  notes               TEXT,
  event_metadata_json JSONB,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ppe_position_id ON paper_position_events(position_id);
CREATE INDEX idx_ppe_signal_id ON paper_position_events(signal_id);
CREATE INDEX idx_ppe_event_type ON paper_position_events(event_type);
CREATE INDEX idx_ppe_timestamp ON paper_position_events(timestamp);

-- ══════════════════════════════════════════════════════════════════
-- 4. agent_control — single-row command table
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE agent_control (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status      TEXT NOT NULL DEFAULT 'RUNNING',   -- RUNNING / PAUSED / EMERGENCY_CLOSE
  updated_by  TEXT,
  reason      TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default row
INSERT INTO agent_control (status, updated_by) VALUES ('RUNNING', 'migration');

-- ══════════════════════════════════════════════════════════════════
-- 5. agent_state — agent heartbeat / diagnostics
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE agent_state (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_name                TEXT NOT NULL DEFAULT 'paper_trader',
  status                    TEXT NOT NULL DEFAULT 'STARTING',
  last_heartbeat            TIMESTAMPTZ,
  last_signal_poll_at       TIMESTAMPTZ,
  last_position_manage_at   TIMESTAMPTZ,
  open_position_count       INTEGER NOT NULL DEFAULT 0,
  total_signals_executed    INTEGER NOT NULL DEFAULT 0,
  total_positions_closed    INTEGER NOT NULL DEFAULT 0,
  notes                     TEXT,
  metadata_json             JSONB,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default row
INSERT INTO agent_state (agent_name, status) VALUES ('paper_trader', 'STARTING');

-- ══════════════════════════════════════════════════════════════════
-- 6. paper_account — aggregate stats (updated by engine)
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE paper_account (
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

INSERT INTO paper_account (balance, allocated, free) VALUES (25000.00, 0.00, 25000.00);
