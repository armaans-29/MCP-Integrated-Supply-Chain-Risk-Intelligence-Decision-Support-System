"""MCP server: supplier / logistics tracking adapter.
Live: GET {LOGISTICS_API_URL}/shipments (see README for schema). Demo: shipments stored in the local database (flagged)."""
from __future__ import annotations
from .. import analytics as A, db
from ..config import env
from .common import err, http_get, make, ok

mcp = make("sentinel-logistics", "logistics")
FIELDS = ["tracking_number", "po_number", "supplier", "materials", "carrier", "status", "origin", "destination", "eta_original",
          "eta_current", "delay_days", "delayed", "last_event", "current_location", "lat", "lon"]

def _live() -> bool:
    return bool(env("LOGISTICS_API_URL"))

def _headers():
    return {"Authorization": f"Bearer {env('LOGISTICS_API_KEY')}"} if env("LOGISTICS_API_KEY") else {}

def _fetch_live():
    good, d = http_get(env("LOGISTICS_API_URL").rstrip("/") + "/shipments", headers=_headers())
    if not good:
        return False, d
    raw = d.get("shipments", d) if isinstance(d, dict) else d
    local = {r["tracking_number"]: r for r in A.records(A.shipments_table())}
    out = []
    for s in raw:
        base = local.get(s.get("tracking_number"), {})
        eta = s.get("eta") or s.get("eta_current")
        o = {**{k: base.get(k) for k in FIELDS}, "tracking_number": s.get("tracking_number"), "status": s.get("status"), "eta_current": eta,
             "last_event": s.get("last_event"), "current_location": s.get("location"), "lat": s.get("lat"), "lon": s.get("lon")}
        if o.get("eta_original") and eta:
            from datetime import date
            o["delay_days"] = (date.fromisoformat(str(eta)[:10]) - date.fromisoformat(o["eta_original"][:10])).days
            o["delayed"] = o["delay_days"] > 0
        out.append(o)
    return True, out

def _all():
    if _live():
        good, d = _fetch_live()
        return (ok(d, "live", "Logistics API") if good else err(d, "live"))
    d = A.records(A.shipments_table())
    return ok([{k: r.get(k) for k in FIELDS} for r in d if r["active"] or True], "demo", "demo adapter (local database)",
              note="LOGISTICS_API_URL not set – demo shipment data")

@mcp.tool()
def logistics_status() -> dict:
    """Health check for the logistics API adapter."""
    if not _live():
        db.set_integration("logistics", "demo", "demo", "LOGISTICS_API_URL not set – demo data")
        return ok({"detail": "No API configured – demo shipment data."}, "demo", "demo adapter")
    good, d = http_get(env("LOGISTICS_API_URL").rstrip("/") + "/shipments", headers=_headers(), timeout=8, retries=0)
    db.set_integration("logistics", "live", "connected" if good else "error", "reachable" if good else str(d))
    return ok({"detail": "Logistics API reachable"}, "live", "Logistics API") if good else err(str(d), "live")

@mcp.tool()
def get_shipment_status(delayed_only: bool = False, tracking_number: str = "", po_number: str = "", active_only: bool = True) -> dict:
    """Shipment tracking: status, original vs current ETA, delay days, last event and position (lat/lon) per shipment."""
    r = _all()
    if not r["ok"]:
        return r
    d = r["data"]
    if tracking_number:
        d = [x for x in d if x["tracking_number"] == tracking_number]
    if po_number:
        d = [x for x in d if x.get("po_number") == po_number]
    if delayed_only:
        d = [x for x in d if x.get("delayed")]
    if active_only:
        d = [x for x in d if x.get("status") != "Delivered"]
    return {**r, "data": d}

@mcp.tool()
def get_supplier_delivery_status(supplier_name: str) -> dict:
    """Delivery status for one supplier's open shipments and purchase orders (name match, case-insensitive)."""
    r = _all()
    if not r["ok"]:
        return r
    ships = [x for x in r["data"] if supplier_name.lower() in str(x.get("supplier", "")).lower() and x.get("status") != "Delivered"]
    pos = db.rows("SELECT p.po_number, p.promised_date, p.status FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id "
                  "WHERE LOWER(s.name) LIKE :n AND p.status IN ('open','confirmed','in_transit','delayed')", n=f"%{supplier_name.lower()}%")
    return {**r, "data": {"shipments": ships, "open_purchase_orders": pos}}

if __name__ == "__main__":
    db.init_db()
    mcp.run(transport="streamable-http")
