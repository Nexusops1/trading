"""Seed test data into paper_positions for visual verification."""

import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"])

now = datetime.now(timezone.utc)

positions = [
    {
        "signal_id": "SEED-001",
        "ticker": "TSLA",
        "direction": "SHORT",
        "setup_family": "hunt_mode",
        "grade": "A+",
        "confidence": 92.5,
        "market_state": "HUNT MODE",
        "entry_time": (now - timedelta(minutes=23)).isoformat(),
        "entry_stock_price": 381.20,
        "entry_premium": 6.80,
        "contracts": 1,
        "cost_basis": 680.00,
        "current_premium": 8.20,
        "current_stock_price": 374.50,
        "current_pnl": 140.00,
        "tp1_premium": 10.20,
        "tp2_premium": 13.60,
        "tp3_premium": 20.40,
        "sl_premium": 3.40,
        "tp1_stock": 375.00,
        "tp2_stock": 370.00,
        "tp3_stock": 360.00,
        "sl_stock": 388.00,
        "tp1_hit": True,
        "tp2_hit": False,
        "tp3_hit": False,
        "stop_at_breakeven": True,
        "status": "OPEN",
        "agent_note": "MONITORING — Stop moved to breakeven",
    },
    {
        "signal_id": "SEED-002",
        "ticker": "NVDA",
        "direction": "SHORT",
        "setup_family": "hunt_mode",
        "grade": "A",
        "confidence": 87.0,
        "market_state": "HUNT MODE",
        "entry_time": (now - timedelta(minutes=15)).isoformat(),
        "entry_stock_price": 142.80,
        "entry_premium": 2.64,
        "contracts": 1,
        "cost_basis": 264.00,
        "current_premium": 2.10,
        "current_stock_price": 144.20,
        "current_pnl": -54.00,
        "tp1_premium": 3.96,
        "tp2_premium": 5.28,
        "tp3_premium": 7.92,
        "sl_premium": 1.32,
        "tp1_stock": 140.00,
        "tp2_stock": 137.00,
        "tp3_stock": 132.00,
        "sl_stock": 146.00,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "stop_at_breakeven": False,
        "status": "OPEN",
        "agent_note": "MONITORING — Watching for breakdown below $141",
    },
    {
        "signal_id": "SEED-003",
        "ticker": "AMD",
        "direction": "SHORT",
        "setup_family": "hunt_mode",
        "grade": "A+",
        "confidence": 90.0,
        "market_state": "HUNT MODE",
        "entry_time": (now - timedelta(minutes=45)).isoformat(),
        "entry_stock_price": 118.50,
        "entry_premium": 3.85,
        "contracts": 1,
        "cost_basis": 385.00,
        "current_premium": 4.50,
        "current_stock_price": 116.80,
        "current_pnl": 65.00,
        "tp1_premium": 5.78,
        "tp2_premium": 7.70,
        "tp3_premium": 11.55,
        "sl_premium": 1.93,
        "tp1_stock": 115.00,
        "tp2_stock": 112.00,
        "tp3_stock": 107.00,
        "sl_stock": 122.00,
        "tp1_hit": True,
        "tp2_hit": False,
        "tp3_hit": False,
        "stop_at_breakeven": True,
        "status": "OPEN",
        "agent_note": "MONITORING — TP1 hit, trailing stop active",
    },
]

# Clear any existing seed data
sb.table("paper_positions").delete().like("signal_id", "SEED-%").execute()

for pos in positions:
    sb.table("paper_positions").insert(pos).execute()
    print(f"  Inserted {pos['ticker']} {pos['direction']} @ {pos['entry_premium']}")

# Update account to reflect positions
sb.table("paper_account").update({
    "allocated": 1329.00,
    "free": 23671.00,
    "today_pnl": 151.00,
    "total_pnl": 151.00,
    "total_trades": 0,
    "win_rate": 0,
    "updated_at": now.isoformat(),
}).eq("id", 1).execute()

print("Seed data inserted successfully.")
