"""Central configuration. All secrets come from environment variables / .env – never hard-coded."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()

DATABASE_URL = env("DATABASE_URL") or f"sqlite:///{ROOT / 'data' / 'sentinel.db'}"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

PORTS = {"data": 8101, "sheets": 8102, "slack": 8103, "logistics": 8104, "weather": 8105}
MODULES = {k: f"sentinel.servers.{k}_server" for k in PORTS}
LABELS = {"data": "Database (Supabase/PostgreSQL)", "sheets": "Google Sheets", "slack": "Slack",
          "logistics": "Supplier / Logistics API", "weather": "Weather API"}

def port(key: str) -> int:
    return int(env(f"MCP_PORT_{key.upper()}", str(PORTS[key])))

def bind_host() -> str:
    return env("MCP_BIND_HOST", "127.0.0.1")

def server_url(key: str) -> str:
    return f"http://{env('MCP_HOST', '127.0.0.1')}:{port(key)}/mcp"

ROLES = ["viewer", "analyst", "approver", "admin"]
