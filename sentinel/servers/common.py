"""Shared helpers for MCP servers: response envelope, approval checks, resilient HTTP."""
from __future__ import annotations
import time
import httpx
from mcp.server.fastmcp import FastMCP
from .. import db
from ..config import bind_host, port

def make(title: str, key: str) -> FastMCP:
    return FastMCP(title, host=bind_host(), port=port(key))

def ok(data=None, mode: str = "live", source: str = "", **extra) -> dict:
    return {"ok": True, "mode": mode, "source": source, "data": data, **extra}

def err(message: str, mode: str = "disconnected", **extra) -> dict:
    return {"ok": False, "mode": mode, "error": message, **extra}

def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)

def require_approval(approval_id: int, action: str, expect: dict | None = None) -> str | None:
    """Tool-level permission check. Returns an error string if the action is not validly approved."""
    a = db.get_action(approval_id)
    if not a:
        return f"Approval #{approval_id} does not exist."
    if a["action"] != action:
        return f"Approval #{approval_id} is for '{a['action']}', not '{action}'."
    if a["status"] != "approved":
        return f"Approval #{approval_id} is '{a['status']}' – only 'approved' actions can run."
    if a.get("approved_role") not in ("approver", "admin"):
        return "Approval was not granted by an approver/admin role."
    for k, v in (expect or {}).items():
        if not _same(a["payload"].get(k), v):
            return f"Request differs from what was approved (field '{k}')."
    return None

def http_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 10.0, retries: int = 2):
    """GET with timeout, retry on transient errors and 429 handling. Returns (ok, json_or_error_message)."""
    last = "unknown error"
    for attempt in range(retries + 1):
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in (401, 403):
                return False, f"Authentication failed ({r.status_code}). Check the API key / credentials."
            if r.status_code == 429:
                wait = min(float(r.headers.get("Retry-After", 1)), 5)
                last = "Rate limited (429)."
                time.sleep(wait)
                continue
            r.raise_for_status()
            return True, r.json()
        except httpx.TimeoutException:
            last = f"Timed out after {timeout:.0f}s."
        except httpx.HTTPStatusError as e:
            last = f"HTTP {e.response.status_code} from service."
            if e.response.status_code < 500:
                break
        except Exception as e:  # network / JSON errors
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.4 * (attempt + 1))
    return False, last
