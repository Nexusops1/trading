"""NEXUS Trading Dashboard — FastAPI backend.
All financial stats computed live from paper_positions.
paper_account is kept in sync by a background task but is never read by endpoints.
"""

import os
import threading
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as StarletteJSONResponse
from supabase import create_client
import uvicorn

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stats cache — prevents cascading failures when Supabase is slow
_stats_cache = {"data": None, "ts": 0}
_DEFAULT_STATS = {
    "balance": 1000000, "allocated": 0, "free": 1000000,
    "today_pnl": 0, "today_trades": 0, "today_winners": 0, "today_losers": 0,
    "total_pnl": 0, "total_trades": 0, "win_rate": 0,
    "open_pnl": 0, "open_count": 0,
    "data_error_count": 0, "data_error_pnl": 0,
    "avg_today_pnl": 0, "best_trade_today": 0, "worst_trade_today": 0,
}

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

# ---------------------------------------------------------------------------
# Live stats engine — single source of truth, computed from paper_positions
# ---------------------------------------------------------------------------

def _et_now():
    """Current datetime in US Eastern (EDT = UTC-4)."""
    return datetime.now(timezone.utc) + timedelta(hours=-4)


def _today_start_utc():
    """Midnight ET expressed as UTC ISO string for DB queries."""
    today_et = _et_now().strftime("%Y-%m-%d")
    # today 00:00 ET = 04:00 UTC (EDT)
    return (datetime.strptime(today_et, "%Y-%m-%d").replace(
        tzinfo=timezone.utc) + timedelta(hours=4)).isoformat()


def _compute_live_stats() -> dict:
    """Compute all dashboard stats live from paper_positions.
    Returns cached result if less than 10s old. On DB failure,
    returns last good cache or defaults."""
    global _stats_cache

    # Return cache if fresh (5s TTL)
    if _stats_cache["data"] and (time.time() - _stats_cache["ts"]) < 5:
        return _stats_cache["data"]

    try:
        # ── All closed positions (paginate past 1000 row limit) ────────
        all_closed = []
        page = 0
        page_size = 1000
        while True:
            batch = sb.table("paper_positions").select(
                "realized_pnl_dollars,exit_reason,exit_timestamp"
            ).eq("status", "CLOSED").range(
                page * page_size, (page + 1) * page_size - 1
            ).execute().data or []
            all_closed.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        closed = all_closed

        total_trades = len(closed)
        pnls = [float(t.get("realized_pnl_dollars") or 0) for t in closed]
        total_pnl = round(sum(pnls), 2)
        winners = sum(1 for p in pnls if p > 0)
        win_rate = round(winners / total_trades * 100, 1) if total_trades > 0 else 0

        data_errors = [t for t in closed if t.get("exit_reason") == "DATA_ERROR_CLOSE"]
        data_error_count = len(data_errors)
        data_error_pnl = round(sum(float(t.get("realized_pnl_dollars") or 0) for t in data_errors), 2)

        # ── Today's P&L (paginate past 1000 row limit) ────────
        ts_cutoff = _today_start_utc()
        today_resp = []
        tp_page = 0
        while True:
            tp_batch = sb.table("paper_positions").select(
                "realized_pnl_dollars"
            ).eq("status", "CLOSED").gte(
                "exit_timestamp", ts_cutoff
            ).range(tp_page * page_size, (tp_page + 1) * page_size - 1
            ).execute().data or []
            today_resp.extend(tp_batch)
            if len(tp_batch) < page_size:
                break
            tp_page += 1
        today_pnls = [float(t.get("realized_pnl_dollars") or 0) for t in today_resp]
        today_pnl = round(sum(today_pnls), 2)
        today_trades = len(today_resp)
        today_winners = sum(1 for p in today_pnls if p > 0)
        print(f"[STATS] today_pnl=${today_pnl:.2f} from {today_trades} closed today (cutoff={ts_cutoff})")

        # ── Open positions (paginate) ────────────────────────────────
        open_resp = []
        op_page = 0
        while True:
            op_batch = sb.table("paper_positions").select(
                "entry_price,quantity,unrealized_pnl_dollars"
            ).eq("status", "OPEN").range(
                op_page * page_size, (op_page + 1) * page_size - 1
            ).execute().data or []
            open_resp.extend(op_batch)
            if len(op_batch) < page_size:
                break
            op_page += 1

        allocated = round(sum(
            float(p.get("entry_price") or 0) * int(p.get("quantity") or 1) * 100
            for p in open_resp
        ), 2)
        open_pnl = round(sum(float(p.get("unrealized_pnl_dollars") or 0) for p in open_resp), 2)

        balance = round(1000000 + total_pnl, 2)
        free = round(balance - allocated, 2)

        result = {
            "balance": balance,
            "allocated": allocated,
            "free": free,
            "today_pnl": today_pnl,
            "today_trades": today_trades,
            "today_winners": today_winners,
            "today_losers": today_trades - today_winners,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "open_pnl": open_pnl,
            "open_count": len(open_resp),
            "data_error_count": data_error_count,
            "data_error_pnl": data_error_pnl,
            "avg_today_pnl": round(sum(today_pnls) / len(today_pnls), 2) if today_pnls else 0,
            "best_trade_today": max(today_pnls) if today_pnls else 0,
            "worst_trade_today": min(today_pnls) if today_pnls else 0,
        }
        _stats_cache = {"data": result, "ts": time.time()}
        return result

    except Exception as e:
        print(f"[STATS] DB error (returning cache): {e}")
        return _stats_cache["data"] or _DEFAULT_STATS


# ---------------------------------------------------------------------------
# Background sync — keep paper_account fresh for any external readers
# ---------------------------------------------------------------------------

def _sync_paper_account():
    """Write live stats to paper_account every 60s so external consumers
    that still read paper_account get reasonably fresh data."""
    while True:
        try:
            stats = _compute_live_stats()
            sb.table("paper_account").update({
                "balance": stats["balance"],
                "allocated": stats["allocated"],
                "free": stats["free"],
                "today_pnl": stats["today_pnl"],
                "total_pnl": stats["total_pnl"],
                "total_trades": stats["total_trades"],
                "win_rate": stats["win_rate"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", 1).execute()
        except Exception as e:
            print(f"[SYNC] paper_account sync error: {e}")
        time.sleep(60)


_sync_thread = threading.Thread(target=_sync_paper_account, daemon=True)
_sync_thread.start()
print("[SYNC] paper_account sync thread started — updating every 60s")

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/forge")
async def forge_proxy(request: Request):
    """Proxy Forge requests to Anthropic API. Reads key fresh each request."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(content={"text": "FORGE OFFLINE \u2014 API key not configured"}, status_code=503)
    try:
        body = await request.json()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": body.get("max_tokens", 150),
                    "system": body.get("system", ""),
                    "messages": [{"role": "user", "content": body.get("message", "")}],
                },
            )
        if r.status_code != 200:
            print(f"[FORGE] Anthropic API error: {r.status_code} {r.text[:200]}")
            return JSONResponse(content={"text": "FORGE OFFLINE"}, status_code=502)
        data = r.json()
        text = data.get("content", [{}])[0].get("text", "No response")
        usage = data.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        call_cost = in_tok * 3.00 / 1_000_000 + out_tok * 15.00 / 1_000_000
        # Accumulate total cost in Supabase system_config
        forge_total = call_cost
        try:
            row = sb.table("system_config").select("value").eq("key", "forge_total_cost").limit(1).execute()
            if row.data:
                forge_total = float(row.data[0]["value"]) + call_cost
                sb.table("system_config").update({"value": str(round(forge_total, 6))}).eq("key", "forge_total_cost").execute()
            else:
                forge_total = call_cost
                sb.table("system_config").insert({"key": "forge_total_cost", "value": str(round(forge_total, 6))}).execute()
        except Exception as cost_err:
            print(f"[FORGE] Cost tracking error (non-fatal): {cost_err}")
        return {"text": text, "input_tokens": in_tok, "output_tokens": out_tok, "call_cost": round(call_cost, 6), "forge_total_cost": round(forge_total, 6)}
    except Exception as e:
        print(f"[FORGE] Error: {e}")
        return JSONResponse(content={"text": "FORGE OFFLINE"}, status_code=500)


@app.get("/api/account")
def get_account():
    """Live account stats — computed from paper_positions, never paper_account."""
    try:
        return _compute_live_stats()
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
    try:
        all_open = []
        page = 0
        page_size = 1000
        while True:
            batch = (
                sb.table("paper_positions")
                .select("*")
                .eq("status", "OPEN")
                .order("entry_timestamp", desc=True)
                .range(page * page_size, (page + 1) * page_size - 1)
                .execute()
            ).data or []
            all_open.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
    except Exception as e:
        print(f"[TRADING] positions DB error: {e}")
        return []
    # Transform to dashboard-expected shape
    positions = []
    for p in all_open:
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
            "contract_data": p.get("contract_data"),
            "entry_snapshot": p.get("entry_snapshot") or p.get("entry_snapshot_json"),
            "market_context": _extract_market_context(p),
        })
    return positions


def _extract_market_context(p: dict) -> dict | None:
    """Extract market context at entry from stored metadata."""
    meta = p.get("metadata_json") or {}
    if not meta or not any(meta.get(k) for k in ("vix_spot", "fear_greed_value", "gex_value")):
        return None
    return {
        "vix": meta.get("vix_spot"),
        "fear_greed": meta.get("fear_greed_value"),
        "gex": meta.get("gex_value"),
        "gamma_flip": meta.get("gamma_flip"),
        "call_wall": meta.get("call_wall"),
        "put_wall": meta.get("put_wall"),
        "dealer_bias": meta.get("gex_alignment"),
    }


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
    try:
        all_closed = []
        page = 0
        page_size = 1000
        while True:
            batch = (
                sb.table("paper_positions")
                .select("*")
                .eq("status", "CLOSED")
                .gte("exit_timestamp", today_start_utc)
                .order("exit_timestamp", desc=True)
                .range(page * page_size, (page + 1) * page_size - 1)
                .execute()
            ).data or []
            all_closed.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
    except Exception as e:
        print(f"[TRADING] trades/today DB error: {e}")
        return []
    trades = []
    for p in all_closed:
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
            "strike": p.get("strike"),
            "option_type": p.get("option_type"),
            "expiry": p.get("expiry"),
            "score_total": p.get("score_total"),
            "confluence_count": p.get("confluence_count"),
            "tp1_premium": p.get("tp1_price"),
            "tp2_premium": p.get("tp2_price"),
            "tp3_premium": p.get("tp3_price"),
            "sl_premium": p.get("stop_price"),
            "entry_snapshot": p.get("entry_snapshot"),
            "exit_snapshot": p.get("exit_snapshot"),
            "market_context": _extract_market_context(p),
            "gex_context": p.get("metadata_json") or {},
            "volatility_state": p.get("volatility_state"),
            "agent_state": p.get("agent_state"),
        })
    return trades


@app.get("/api/stats")
def get_stats():
    """Live stats — computed from paper_positions, never paper_account."""
    try:
        s = _compute_live_stats()
    except Exception as e:
        print(f"[TRADING] stats error: {e}")
        s = _stats_cache["data"] or _DEFAULT_STATS
    return {
        "total_today": s["today_trades"],
        "winners_today": s["today_winners"],
        "losers_today": s["today_losers"],
        "win_rate": s["win_rate"],
        "avg_pnl": s["avg_today_pnl"],
        "best_trade": s["best_trade_today"],
        "worst_trade": s["worst_trade_today"],
        "today_pnl": s["today_pnl"],
        "total_pnl": s["total_pnl"],
        "total_trades": s["total_trades"],
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
        open_pos = []
        ns_page = 0
        while True:
            ns_batch = sb.table("paper_positions").select(
                "current_price,stop_price"
            ).eq("status", "OPEN").range(
                ns_page * 1000, (ns_page + 1) * 1000 - 1
            ).execute().data or []
            open_pos.extend(ns_batch)
            if len(ns_batch) < 1000:
                break
            ns_page += 1
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
    try:
        resp = sb.table("agent_control").select("*").limit(1).execute()
        if not resp.data:
            return {"id": None, "status": "RUNNING", "updated_at": None}
        row = resp.data[0]
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
    except Exception as e:
        print(f"[TRADING] agent/status DB error: {e}")
        return {"id": None, "status": "UNKNOWN", "updated_at": None}


def _update_agent_control(status: str, updated_by: str = "dashboard"):
    """Update agent_control — works with UUID PK (limit 1)."""
    try:
        resp = sb.table("agent_control").select("id").limit(1).execute()
        if not resp.data:
            return
        row_id = resp.data[0]["id"]
        sb.table("agent_control").update({
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
        }).eq("id", row_id).execute()
    except Exception as e:
        print(f"[TRADING] agent_control update error: {e}")


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


GATEWAY_LOGIN_URL = os.environ.get("GATEWAY_URL", "https://nexus.praxiumholdings.com")


@app.get("/")
def serve_index(request: Request):
    try:
        verify_jwt(request)
    except Exception:
        return RedirectResponse(url=GATEWAY_LOGIN_URL, status_code=303)
    return FileResponse(str(static_dir / "index.html"))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port)
