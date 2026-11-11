"""MCP server: operational database (Supabase/PostgreSQL, or local SQLite demo).
Exposes controlled, read-mostly operations – there is NO raw SQL tool. The single write tool needs an approved action."""
from __future__ import annotations
import json
import pandas as pd
from .. import analytics as A, db
from ..schemas import InventoryUpdate
from .common import err, make, ok, require_approval

mcp = make("sentinel-data", "data")
MODE = lambda: "demo" if db.is_sqlite() else "live"
SRC = lambda: db.backend_label()
OPEN = "('open','confirmed','in_transit','delayed')"

@mcp.tool()
def data_status() -> dict:
    """Health check for the operational database."""
    try:
        n = db.rows("SELECT COUNT(*) AS n FROM materials")[0]["n"]
        detail = f"{db.backend_label()} reachable – {n} materials"
        db.set_integration("database", MODE(), "connected", detail)
        return ok({"detail": detail, "demo_data": db.is_sqlite()}, mode=MODE(), source=SRC())
    except Exception as e:
        return err(f"Database unreachable: {e}")

@mcp.tool()
def get_inventory_levels(risk_level: str = "", category: str = "", search: str = "") -> dict:
    """Current stock per material with demand, days of cover, reorder point, inbound quantity and stock-out risk class. Optional filters."""
    d = A.inventory_table()
    if risk_level:
        d = d[d.risk.str.lower() == risk_level.lower()]
    if category:
        d = d[d.category.str.lower() == category.lower()]
    if search:
        d = d[d.name.str.contains(search, case=False) | d.sku.str.contains(search, case=False)]
    return ok(A.records(d), MODE(), SRC(), basis="actual stock; days-of-cover is an estimate from 30-day average demand")

@mcp.tool()
def get_supplier_details(supplier_id: int = 0, name: str = "") -> dict:
    """Supplier master data with the materials they supply and their open purchase orders."""
    q = "SELECT * FROM suppliers WHERE (:i=0 OR id=:i) AND (:n='' OR LOWER(name) LIKE :nn)"
    sup = db.rows(q, i=supplier_id, n=name, nn=f"%{name.lower()}%")
    for s in sup:
        s["materials"] = [r["name"] for r in db.rows("SELECT m.name FROM supplier_quotes q JOIN materials m ON m.id=q.material_id WHERE q.supplier_id=:s", s=s["id"])]
        s["open_pos"] = db.rows(f"SELECT po_number, promised_date, status FROM purchase_orders WHERE supplier_id=:s AND status IN {OPEN}", s=s["id"])
    return ok(sup, MODE(), SRC())

@mcp.tool()
def get_supplier_performance(supplier_id: int = 0) -> dict:
    """Supplier KPIs from purchase-order history: on-time rate, lead time and variability, fill rate, OTIF, price variance, score and risk class."""
    d = A.supplier_stats()
    if supplier_id:
        d = d[d.supplier_id == supplier_id]
    return ok(A.records(d.sort_values("score", ascending=False)), MODE(), SRC(), basis="actual history; score is a weighted heuristic")

@mcp.tool()
def get_purchase_orders(status: str = "open", supplier_id: int = 0, limit: int = 100) -> dict:
    """Purchase orders with value and line items. status: open | received | delayed | in_transit | confirmed | all."""
    where, p = ["1=1"], {"lim": min(max(limit, 1), 500)}
    if status == "open":
        where.append(f"p.status IN {OPEN}")
    elif status not in ("all", ""):
        where.append("p.status=:st"); p["st"] = status
    if supplier_id:
        where.append("p.supplier_id=:sid"); p["sid"] = supplier_id
    d = db.df(f"""SELECT p.id AS po_id, p.po_number, s.name AS supplier, p.order_date, p.promised_date, p.received_date, p.status,
                         SUM(i.qty_ordered*i.unit_price) AS value
                  FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id JOIN purchase_order_items i ON i.po_id=p.id
                  WHERE {' AND '.join(where)} GROUP BY p.id, p.po_number, s.name, p.order_date, p.promised_date, p.received_date, p.status
                  ORDER BY p.promised_date DESC LIMIT :lim""", **p)
    if not d.empty:
        it = db.df("SELECT i.po_id, m.name, m.unit, i.qty_ordered, i.qty_received FROM purchase_order_items i JOIN materials m ON m.id=i.material_id")
        it["t"] = it.name + " ×" + it.qty_ordered.round(0).astype(int).astype(str) + " " + it.unit
        d = d.merge(it.groupby("po_id").t.apply("; ".join).rename("items").reset_index(), on="po_id", how="left")
    return ok(A.records(d), MODE(), SRC())

@mcp.tool()
def get_production_orders(status: str = "open") -> dict:
    """Production orders with bill of materials. status: open | completed | all."""
    st = {"open": "('planned','in_progress')", "completed": "('completed')", "all": "('planned','in_progress','completed')"}.get(status, "('planned','in_progress')")
    d = db.rows(f"SELECT * FROM production_orders WHERE status IN {st} ORDER BY due_date")
    for o in d:
        o["bom"] = db.rows("SELECT m.name AS material, m.unit, b.qty_required FROM production_bom b JOIN materials m ON m.id=b.material_id WHERE b.production_order_id=:i", i=o["id"])
    return ok(d, MODE(), SRC())

@mcp.tool()
def calculate_stockout_risk(horizon_days: int = 7, min_level: str = "Medium") -> dict:
    """Rank materials by stock-out risk (Critical/High/Medium/Low). Compares days of cover with next inbound arrival or supplier lead time."""
    return ok(A.stockout_risk(horizon_days, min_level), MODE(), SRC(), basis="estimate",
              method="cover = available ÷ 30-day average daily demand; gap = (next inbound ETA or lead time) − cover")

@mcp.tool()
def calculate_production_impact() -> dict:
    """Check every open production order's BOM against stock and inbound ETAs. Returns estimated delay days, uncovered materials and value at risk."""
    return ok(A.production_impact(), MODE(), SRC(), basis="estimate")

@mcp.tool()
def compare_supplier_options(material: str, quantity: float = 0) -> dict:
    """Compare quoted suppliers for a material (name, SKU or id): price, lead time, reliability and total cost for the quantity."""
    r = A.compare_suppliers(material, quantity)
    return err(r["error"], MODE()) if "error" in r else ok(r, MODE(), SRC(), basis="quotes + actual history")

@mcp.tool()
def generate_replenishment_plan() -> dict:
    """Suggested purchase quantities and suppliers for materials below reorder point or at Critical/High risk."""
    return ok(A.replenishment_plan(), MODE(), SRC(), basis="estimate")

@mcp.tool()
def get_kpis() -> dict:
    """Supply-chain KPIs (turnover, DOH, OTD, OTIF, lead time, cost variance, delay rate …). Unavailable KPIs say why."""
    return ok(A.kpis(), MODE(), SRC())

@mcp.tool()
def get_risk_alerts(status: str = "open") -> dict:
    """Risk and alert centre records (rule-detected). status: open | cleared | resolved | '' for all."""
    return ok(A.alerts(status), MODE(), SRC())

@mcp.tool()
def run_risk_detection() -> dict:
    """Run the rule-based risk detector and persist alerts."""
    return ok(A.detect_risks(), MODE(), SRC())

@mcp.tool()
def update_inventory_record(sku: str, new_on_hand: float, reason: str, approval_id: int) -> dict:
    """CONTROLLED WRITE: set on-hand quantity for one SKU. Requires an approval (action 'update_inventory') granted by an approver/admin whose payload matches."""
    try:
        req = InventoryUpdate(sku=sku, new_on_hand=new_on_hand, reason=reason, approval_id=approval_id)
    except Exception as e:
        return err(f"Invalid request: {e}", MODE())
    bad = require_approval(req.approval_id, "update_inventory", {"sku": req.sku, "new_on_hand": req.new_on_hand})
    if bad:
        return err(bad, MODE())
    row = db.rows("SELECT m.id, i.on_hand FROM materials m JOIN inventory i ON i.material_id=m.id WHERE m.sku=:s", s=req.sku)
    if not row:
        return err(f"SKU {req.sku} not found.", MODE())
    delta = req.new_on_hand - row[0]["on_hand"]
    db.execute("UPDATE inventory SET on_hand=:q, updated_at=:n WHERE material_id=:m", q=req.new_on_hand, n=db.now(), m=row[0]["id"])
    db.execute("INSERT INTO inventory_movements (material_id, movement_date, type, quantity, reference) VALUES (:m,:d,'adjustment',:q,:r)",
               m=row[0]["id"], d=db.now()[:10], q=delta, r=f"approval#{req.approval_id}: {req.reason}")
    after = db.rows("SELECT on_hand FROM inventory WHERE material_id=:m", m=row[0]["id"])[0]["on_hand"]
    res = {"sku": req.sku, "previous": row[0]["on_hand"], "new": after, "verified": abs(after - req.new_on_hand) < 1e-6}
    db.set_action(req.approval_id, status="executed", result=res)
    return ok(res, MODE(), SRC())

if __name__ == "__main__":
    db.init_db()
    mcp.run(transport="streamable-http")
