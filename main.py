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

app = FastAPI(title="NEXUS Trading Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(AuthMiddleware)

# ── Auto-bump paper account from 25k to 100k if needed ────────────────────
try:
    _acct = sb.table("paper_account").select("id,balance").limit(1).execute()
    if _acct.data and float(_acct.data[0].get("balance", 0)) < 1000000:
        allocated = float(_acct.data[0].get("allocated", 0))
        sb.table("paper_account").update({
            "balance": 1000000, "free": round(1000000 - allocated, 2),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", _acct.data[0]["id"]).execute()
        print("[TRADING] Paper account bumped to $1,000,000")
except Exception as e:
    print(f"[TRADING] Account bump check failed: {e}")

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/account")
def get_account():
    resp = sb.table("paper_account").select("*").limit(1).execute()
    acct = resp.data[0] if resp.data else {
        "balance": 1000000, "allocated": 0, "free": 1000000,
        "today_pnl": 0, "total_pnl": 0, "total_trades": 0, "win_rate": 0,
    }
    # Compute running open P&L from all OPEN positions
    try:
        open_resp = sb.table("paper_positions").select("unrealized_pnl_dollars").eq("status", "OPEN").execute()
        open_pnl = sum(float(p.get("unrealized_pnl_dollars") or 0) for p in (open_resp.data or []))
        acct["open_pnl"] = round(open_pnl, 2)
    except Exception:
        acct["open_pnl"] = 0
    return acct


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
    """Closed positions from today — from paper_positions where status=CLOSED."""
    today_str = date.today().isoformat()
    resp = (
        sb.table("paper_positions")
        .select("*")
        .eq("status", "CLOSED")
        .gte("exit_timestamp", f"{today_str}T00:00:00")
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
    today_str = date.today().isoformat()
    trades = (
        sb.table("paper_positions")
        .select("realized_pnl_dollars,realized_pnl_percent,exit_reason")
        .eq("status", "CLOSED")
        .gte("exit_timestamp", f"{today_str}T00:00:00")
        .execute()
    ).data or []

    account = (
        sb.table("paper_account").select("win_rate,total_trades,today_pnl,total_pnl").limit(1).execute()
    ).data

    pnls = [float(t.get("realized_pnl_dollars") or 0) for t in trades]
    winners = [p for p in pnls if p > 0]

    return {
        "total_today": len(trades),
        "winners_today": len(winners),
        "losers_today": len(trades) - len(winners),
        "win_rate": account[0]["win_rate"] if account else 0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "best_trade": max(pnls) if pnls else 0,
        "worst_trade": min(pnls) if pnls else 0,
        "today_pnl": account[0]["today_pnl"] if account else 0,
        "total_pnl": account[0]["total_pnl"] if account else 0,
        "total_trades": account[0]["total_trades"] if account else 0,
    }


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
