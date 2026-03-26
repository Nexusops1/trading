"""NEXUS Trading Dashboard — FastAPI backend.
Reads from LAB MODE paper_positions schema (v2).
"""

import os
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as StarletteJSONResponse
from supabase import create_client
import uvicorn

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

from auth import verify_jwt

_PUBLIC_PATHS = {"/", "/api/health"}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)
        try:
            verify_jwt(request)
        except Exception:
            return StarletteJSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NEXUS Trading Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nexus.praxiumholdings.com",
        "https://trading.praxiumholdings.com",
        "https://execution.praxiumholdings.com",
        "https://core.praxiumholdings.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

# Note: /api/account now computes live from paper_positions.
# The paper_account table is no longer the source of truth for the dashboard.

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/account")
def get_account():
    """Live account stats computed from paper_positions — never stale."""
    try:
        # ── All closed positions ──────────────────────────────────────
        closed = sb.table("paper_positions").select(
            "realized_pnl_dollars,exit_reason,exit_timestamp"
        ).eq("status", "CLOSED").execute().data or []

        # total_trades = count of all closed positions (all time)
        total_trades = len(closed)
        pnls = [float(t.get("realized_pnl_dollars") or 0) for t in closed]
        # total_pnl = sum of realized_pnl_dollars for ALL closed positions
        total_pnl = round(sum(pnls), 2)
        # win_rate = profitable closed / total closed * 100
        winners = sum(1 for p in pnls if p > 0)
        win_rate = round(winners / total_trades * 100, 1) if total_trades > 0 else 0

        # DATA_ERROR_CLOSE tracking
        data_errors = [t for t in closed if t.get("exit_reason") == "DATA_ERROR_CLOSE"]
        data_error_count = len(data_errors)
        data_error_pnl = round(sum(float(t.get("realized_pnl_dollars") or 0) for t in data_errors), 2)

        # ── Today's P&L (ET timezone) ────────────────────────────────
        now_utc = datetime.now(timezone.utc)
        et_offset = timedelta(hours=-4)  # EDT
        now_et = now_utc + et_offset
        today_et = now_et.strftime("%Y-%m-%d")
        # today 00:00 ET = 04:00 UTC
        today_start_utc = (datetime.strptime(today_et, "%Y-%m-%d").replace(
            tzinfo=timezone.utc) - et_offset).isoformat()
        today_pnl = round(sum(
            float(t.get("realized_pnl_dollars") or 0) for t in closed
            if (t.get("exit_timestamp") or "") >= today_start_utc
        ), 2)

        # ── Open positions ────────────────────────────────────────────
        open_resp = sb.table("paper_positions").select(
            "entry_price,quantity,unrealized_pnl_dollars"
        ).eq("status", "OPEN").execute().data or []

        # allocated = sum of (entry_price * 100 * quantity) for all OPEN positions
        allocated = round(sum(
            float(p.get("entry_price") or 0) * int(p.get("quantity") or 1) * 100
            for p in open_resp
        ), 2)
        # open_pnl = sum of unrealized_pnl_dollars for all OPEN positions
        open_pnl = round(sum(float(p.get("unrealized_pnl_dollars") or 0) for p in open_resp), 2)

        # balance = starting capital ($1M) + all-time realized P&L
        balance = round(1000000 + total_pnl, 2)
        # free = balance - allocated (cash not in open positions)
        free = round(balance - allocated, 2)

        return {
            "balance": balance,
            "allocated": allocated,
            "free": free,
            "today_pnl": today_pnl,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "open_pnl": open_pnl,
            "data_error_count": data_error_count,
            "data_error_pnl": data_error_pnl,
        }
    except Exception as e:
        print(f"[TRADING] get_account error: {e}")
        return {
            "balance": 1000000, "allocated": 0, "free": 1000000,
            "today_pnl": 0, "total_pnl": 0, "total_trades": 0,
            "win_rate": 0, "open_pnl": 0,
        }


@app.get("/api/positions")
def get_positions():
    """Open positions — maps LAB MODE schema to dashboard format."""
    resp = (
        sb.table("paper_positions")
        .select("*")
        .eq("status", "OPEN")
        .order("entry_timestamp", desc=True)
        .execute()
    )
    # Transform to dashboard-expected shape
    positions = []
    for p in (resp.data or []):
        entry_price = float(p.get("entry_price") or 0)
        current_price = float(p.get("current_price") or entry_price)
        pnl = float(p.get("unrealized_pnl_dollars") or 0)

        positions.append({
            "id": p["id"],
            "signal_id": p.get("signal_id"),
            "ticker": p.get("ticker", ""),
            "direction": (p.get("direction") or "").upper(),
            "setup_family": p.get("setup_family"),
            "grade": p.get("grade"),
            "confidence": p.get("confidence"),
            "market_state": p.get("market_state"),
            "entry_time": p.get("entry_timestamp"),
            "entry_stock_price": p.get("underlying_entry_price"),
            "entry_premium": entry_price,
            "contracts": p.get("quantity", 1),
            "cost_basis": round(entry_price * 100, 2),
            "current_premium": current_price,
            "current_stock_price": p.get("underlying_current_price"),
            "current_pnl": pnl,
            "tp1_premium": p.get("tp1_price"),
            "tp2_premium": p.get("tp2_price"),
            "tp3_premium": p.get("tp3_price"),
            "sl_premium": p.get("stop_price"),
            "tp1_stock": None,
            "tp2_stock": None,
            "tp3_stock": None,
            "sl_stock": None,
            "tp1_hit": p.get("hit_tp1", False),
            "tp2_hit": p.get("hit_tp2", False),
            "tp3_hit": p.get("hit_tp3", False),
            "stop_at_breakeven": p.get("agent_state") == "AT_BREAKEVEN",
            "status": p.get("status", "OPEN"),
            "expiry": p.get("expiry"),
            "strike": p.get("strike"),
            "option_type": p.get("option_type"),
            "option_contract": p.get("option_contract"),
            "score_total": p.get("score_total"),
            "confluence_count": p.get("confluence_count"),
            "gamma_context": p.get("gamma_context"),
            "volatility_state": p.get("volatility_state"),
            "vix_context": p.get("vix_context"),
            "agent_state": p.get("agent_state"),
            "sequence_index": p.get("sequence_index_for_ticker"),
            "sibling_count": p.get("sibling_signal_count_nearby"),
            "time_in_trade_seconds": p.get("time_in_trade_seconds"),
            "agent_note": _build_agent_note(p),
            "gex_context": p.get("metadata_json") or {},
        })
    return positions


def _build_agent_note(p: dict) -> str:
    """Build a human-readable agent note from position state."""
    state = p.get("agent_state", "MONITORING")
    notes = []
    if state == "AT_BREAKEVEN":
        notes.append("Stop at breakeven")
    elif state == "TP2_HIT":
        notes.append("TP2 hit, trailing")
    elif state == "TP1_HIT":
        notes.append("TP1 hit")
    else:
        notes.append("Monitoring")

    failures = p.get("consecutive_quote_failures", 0)
    if failures > 0:
        notes.append(f"Quote miss x{failures}")

    option = p.get("option_contract")
    if option:
        notes.append(option)

    return " | ".join(notes)


@app.get("/api/trades/today")
def get_trades_today():
    """Closed positions from today (ET timezone) — from paper_positions where status=CLOSED."""
    now_utc = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-4)  # EDT
    now_et = now_utc + et_offset
    today_et = now_et.strftime("%Y-%m-%d")
    # today 00:00 ET = 04:00 UTC
    today_start_utc = (datetime.strptime(today_et, "%Y-%m-%d").replace(
        tzinfo=timezone.utc) - et_offset).isoformat()
    resp = (
        sb.table("paper_positions")
        .select("*")
        .eq("status", "CLOSED")
        .gte("exit_timestamp", today_start_utc)
        .order("exit_timestamp", desc=True)
        .execute()
    )
    trades = []
    for p in (resp.data or []):
        entry_price = float(p.get("entry_price") or 0)
        exit_price = float(p.get("current_price") or entry_price)
        trades.append({
            "id": p["id"],
            "signal_id": p.get("signal_id"),
            "ticker": p.get("ticker"),
            "direction": (p.get("direction") or "").upper(),
            "setup_family": p.get("setup_family"),
            "grade": p.get("grade"),
            "confidence": p.get("confidence"),
            "market_state": p.get("market_state"),
            "entry_time": p.get("entry_timestamp"),
            "exit_time": p.get("exit_timestamp"),
            "time_in_trade_minutes": (p.get("time_in_trade_seconds") or 0) // 60,
            "entry_premium": entry_price,
            "exit_premium": exit_price,
            "entry_stock_price": p.get("underlying_entry_price"),
            "exit_stock_price": p.get("underlying_current_price"),
            "contracts": p.get("quantity", 1),
            "cost_basis": round(entry_price * 100, 2),
            "proceeds": round(exit_price * 100, 2),
            "pnl_dollars": p.get("realized_pnl_dollars"),
            "pnl_pct": p.get("realized_pnl_percent"),
            "exit_reason": p.get("exit_reason"),
            "tp1_hit": p.get("hit_tp1", False),
            "tp2_hit": p.get("hit_tp2", False),
            "tp3_hit": p.get("hit_tp3", False),
            "max_favorable_excursion": p.get("max_favorable_excursion"),
            "max_adverse_excursion": p.get("max_adverse_excursion"),
        })
    return trades


@app.get("/api/stats")
def get_stats():
    """Live stats computed from paper_positions — never reads stale paper_account."""
    # Today's closed trades (ET timezone)
    now_utc = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-4)  # EDT
    now_et = now_utc + et_offset
    today_et = now_et.strftime("%Y-%m-%d")
    today_start_utc = (datetime.strptime(today_et, "%Y-%m-%d").replace(
        tzinfo=timezone.utc) - et_offset).isoformat()

    today_trades = (
        sb.table("paper_positions")
        .select("realized_pnl_dollars,realized_pnl_percent,exit_reason")
        .eq("status", "CLOSED")
        .gte("exit_timestamp", today_start_utc)
        .execute()
    ).data or []

    today_pnls = [float(t.get("realized_pnl_dollars") or 0) for t in today_trades]
    today_winners = [p for p in today_pnls if p > 0]
    today_pnl = round(sum(today_pnls), 2)

    # All-time closed trades
    all_closed = sb.table("paper_positions").select(
        "realized_pnl_dollars"
    ).eq("status", "CLOSED").execute().data or []
    all_pnls = [float(t.get("realized_pnl_dollars") or 0) for t in all_closed]
    total_pnl = round(sum(all_pnls), 2)
    total_trades = len(all_closed)
    all_winners = sum(1 for p in all_pnls if p > 0)
    win_rate = round(all_winners / total_trades * 100, 1) if total_trades > 0 else 0

    return {
        "total_today": len(today_trades),
        "winners_today": len(today_winners),
        "losers_today": len(today_trades) - len(today_winners),
        "win_rate": win_rate,
        "avg_pnl": round(sum(today_pnls) / len(today_pnls), 2) if today_pnls else 0,
        "best_trade": max(today_pnls) if today_pnls else 0,
        "worst_trade": min(today_pnls) if today_pnls else 0,
        "today_pnl": today_pnl,
        "total_pnl": total_pnl,
        "total_trades": total_trades,
    }


@app.get("/api/debug")
def get_debug():
    """Temporary debug endpoint — shows raw DB values for diagnosis."""
    now_utc = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-4)
    now_et = now_utc + et_offset
    today_et = now_et.strftime("%Y-%m-%d")
    today_start_utc = (datetime.strptime(today_et, "%Y-%m-%d").replace(
        tzinfo=timezone.utc) - et_offset).isoformat()

    try:
        # All closed
        all_closed = sb.table("paper_positions").select(
            "realized_pnl_dollars"
        ).eq("status", "CLOSED").execute().data or []
        total_pnl_raw = round(sum(float(t.get("realized_pnl_dollars") or 0) for t in all_closed), 2)

        # Closed today
        today_closed = sb.table("paper_positions").select(
            "realized_pnl_dollars"
        ).eq("status", "CLOSED").gte("exit_timestamp", today_start_utc).execute().data or []
        today_pnl_raw = round(sum(float(t.get("realized_pnl_dollars") or 0) for t in today_closed), 2)

        # Open positions
        open_pos = sb.table("paper_positions").select(
            "entry_price,quantity"
        ).eq("status", "OPEN").execute().data or []
        allocated = round(sum(
            float(p.get("entry_price") or 0) * int(p.get("quantity") or 1) * 100
            for p in open_pos
        ), 2)

        # Check for NULLs in realized_pnl_dollars
        null_pnl = sb.table("paper_positions").select(
            "id,ticker,exit_reason", count="exact"
        ).eq("status", "CLOSED").is_("realized_pnl_dollars", "null").execute()

        balance = round(1000000 + total_pnl_raw, 2)

        return {
            "balance": balance,
            "allocated_from_db": allocated,
            "free_calculated": round(balance - allocated, 2),
            "today_pnl_raw": today_pnl_raw,
            "total_pnl_raw": total_pnl_raw,
            "closed_today_count": len(today_closed),
            "closed_total_count": len(all_closed),
            "open_count": len(open_pos),
            "null_pnl_count": null_pnl.count or 0,
            "timezone_check": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "today_start_utc": today_start_utc,
            "stale_paper_account": sb.table("paper_account").select("*").limit(1).execute().data,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/system-stats")
def get_system_stats():
    """System stats for the dashboard stat bars."""
    now = datetime.now(timezone.utc)
    result = {
        "near_stop_count": 0,
        "quote_fail_count": 0,
        "last_heartbeat": None,
        "heartbeat_age_seconds": None,
    }
    try:
        # Near stop: positions where current_price <= stop_price * 1.10
        open_pos = sb.table("paper_positions").select(
            "current_price,stop_price"
        ).eq("status", "OPEN").execute().data or []
        near = 0
        for p in open_pos:
            cp = float(p.get("current_price") or 0)
            sp = float(p.get("stop_price") or 0)
            if sp > 0 and cp > 0 and cp <= sp * 1.10:
                near += 1
        result["near_stop_count"] = near

        # Quote failures in last 15 min
        cutoff = (now - timedelta(minutes=15)).isoformat()
        qf = sb.table("paper_position_events").select(
            "id", count="exact"
        ).eq("event_type", "QUOTE_MISSING").gte("timestamp", cutoff).execute()
        result["quote_fail_count"] = qf.count or 0

        # Heartbeat
        state = sb.table("agent_state").select(
            "last_heartbeat"
        ).eq("agent_name", "paper_trader").limit(1).execute()
        if state.data and state.data[0].get("last_heartbeat"):
            hb = state.data[0]["last_heartbeat"]
            result["last_heartbeat"] = hb
            hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
            result["heartbeat_age_seconds"] = int((now - hb_dt).total_seconds())
    except Exception as e:
        print(f"[TRADING] system-stats error: {e}")
    return result


@app.get("/api/agent/status")
def get_agent_status():
    """Read agent_control (UUID-based, single row)."""
    resp = sb.table("agent_control").select("*").limit(1).execute()
    if not resp.data:
        return {"id": None, "status": "RUNNING", "updated_at": None}
    row = resp.data[0]
    # Also fetch agent_state for richer info
    state_resp = sb.table("agent_state").select("*").eq("agent_name", "paper_trader").limit(1).execute()
    agent_state = state_resp.data[0] if state_resp.data else {}
    return {
        "id": row.get("id"),
        "status": row.get("status", "RUNNING"),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
        "open_position_count": agent_state.get("open_position_count", 0),
        "last_heartbeat": agent_state.get("last_heartbeat"),
        "total_signals_executed": agent_state.get("total_signals_executed", 0),
        "total_positions_closed": agent_state.get("total_positions_closed", 0),
    }


def _update_agent_control(status: str, updated_by: str = "dashboard"):
    """Update agent_control — works with UUID PK (limit 1)."""
    resp = sb.table("agent_control").select("id").limit(1).execute()
    if not resp.data:
        return
    row_id = resp.data[0]["id"]
    sb.table("agent_control").update({
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by,
    }).eq("id", row_id).execute()


@app.post("/api/agent/pause")
def pause_agent():
    _update_agent_control("PAUSED")
    return {"status": "PAUSED"}


@app.post("/api/agent/resume")
def resume_agent():
    _update_agent_control("RUNNING")
    return {"status": "RUNNING"}


@app.post("/api/agent/emergency-close")
def emergency_close():
    _update_agent_control("EMERGENCY_CLOSE")
    return {"status": "EMERGENCY_CLOSE"}


# ---------------------------------------------------------------------------
# Static files / SPA
# ---------------------------------------------------------------------------

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_index():
    return FileResponse(str(static_dir / "index.html"))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port)
