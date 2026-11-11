"""Optional FastAPI backend (read-only):  uvicorn sentinel.api:app --port 8000"""
import uuid
from fastapi import FastAPI
from pydantic import BaseModel, Field
from . import analytics as A, db
from .mcp_client import get_hub

app = FastAPI(title="SupplyChain Sentinel API", version="1.0")

class Ask(BaseModel):
    query: str = Field(min_length=3, max_length=500)

@app.on_event("startup")
def _init():
    db.init_db()

@app.get("/health")
def health():
    return {"status": "ok", "database": db.backend_label(), "integrations": [{k: h[k] for k in ("service", "server", "mode", "detail")} for h in get_hub().health()]}

@app.get("/kpis")
def kpis():
    return A.kpis()

@app.get("/overview")
def overview():
    return A.overview()

@app.post("/ask")
def ask(body: Ask):
    from .agent import SentinelAgent
    out = SentinelAgent().ask(body.query, str(uuid.uuid4()))
    if out.get("pending_approval"):
        out["answer"] += "\n\n(An external action needs approval – use the Streamlit UI to approve it.)"
    return {k: out.get(k) for k in ("intent", "answer", "recs", "sources", "trace", "errors", "pending_approval")}
