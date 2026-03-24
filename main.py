"""NEXUS Trading Dashboard — FastAPI backend."""

import os
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from supabase import create_client
import uvicorn

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="NEXUS Trading Dashboard")

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/account")
def get_account():
    resp = sb.table("paper_account").select("*").limit(1).execute()
    if not resp.data:
        raise HTTPException(404, "No account row found")
    return resp.data[0]


@app.get("/api/positions")
def get_positions():
    resp = (
        sb.table("paper_positions")
        .select("*")
        .eq("status", "OPEN")
        .order("entry_time", desc=True)
        .execute()
    )
    return resp.data


@app.get("/api/trades/today")
def get_trades_today():
    today_str = date.today().isoformat()
    resp = (
        sb.table("paper_trades")
        .select("*")
        .gte("exit_time", f"{today_str}T00:00:00")
        .order("exit_time", desc=True)
        .execute()
    )
    return resp.data


@app.get("/api/stats")
def get_stats():
    today_str = date.today().isoformat()
    trades = (
        sb.table("paper_trades")
        .select("pnl_dollars,pnl_pct,exit_reason")
        .gte("exit_time", f"{today_str}T00:00:00")
        .execute()
    ).data

    account = (
        sb.table("paper_account").select("win_rate,total_trades,today_pnl,total_pnl").limit(1).execute()
    ).data

    if not trades:
        return {
            "total_today": 0,
            "winners_today": 0,
            "losers_today": 0,
            "win_rate": account[0]["win_rate"] if account else 0,
            "avg_pnl": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "today_pnl": account[0]["today_pnl"] if account else 0,
            "total_pnl": account[0]["total_pnl"] if account else 0,
            "total_trades": account[0]["total_trades"] if account else 0,
        }

    pnls = [t["pnl_dollars"] or 0 for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    return {
        "total_today": len(trades),
        "winners_today": len(winners),
        "losers_today": len(losers),
        "win_rate": account[0]["win_rate"] if account else 0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "best_trade": max(pnls) if pnls else 0,
        "worst_trade": min(pnls) if pnls else 0,
        "today_pnl": account[0]["today_pnl"] if account else 0,
        "total_pnl": account[0]["total_pnl"] if account else 0,
        "total_trades": account[0]["total_trades"] if account else 0,
    }


@app.get("/api/agent/status")
def get_agent_status():
    resp = sb.table("agent_control").select("*").eq("id", 1).execute()
    if not resp.data:
        raise HTTPException(404, "No agent_control row")
    return resp.data[0]


@app.post("/api/agent/pause")
def pause_agent():
    sb.table("agent_control").update({
        "status": "PAUSED",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "dashboard",
    }).eq("id", 1).execute()
    return {"status": "PAUSED"}


@app.post("/api/agent/resume")
def resume_agent():
    sb.table("agent_control").update({
        "status": "RUNNING",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "dashboard",
    }).eq("id", 1).execute()
    return {"status": "RUNNING"}


@app.post("/api/agent/emergency-close")
def emergency_close():
    sb.table("agent_control").update({
        "status": "EMERGENCY_CLOSE",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "dashboard",
    }).eq("id", 1).execute()
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
