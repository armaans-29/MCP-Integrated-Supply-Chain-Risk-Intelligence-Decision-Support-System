"""KPI, risk and recommendation engine. Pure calculations over database records.

Every number is derived from stored data. KPIs whose inputs are missing return value=None with a reason.
Labels:  actual = computed from history · estimate = computed from current data + assumptions · forecast = statistical projection.
"""
from __future__ import annotations
import json, math
from datetime import date
import numpy as np
import pandas as pd
from . import db

OPEN_STATES = ("open", "confirmed", "in_transit", "delayed")
RISK_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "None": 4, "Unknown": 5}

def today() -> date:
    return date.today()

def records(d: pd.DataFrame) -> list[dict]:
    d = d.replace([np.inf, -np.inf], np.nan)
    return json.loads(d.to_json(orient="records", date_format="iso"))

# ───────────────────────── suppliers ─────────────────────────
def _closed_pos() -> pd.DataFrame:
    po = db.df("SELECT id AS po_id, supplier_id, order_date, promised_date, received_date FROM purchase_orders WHERE status='received'")
    it = db.df("SELECT po_id, qty_ordered, qty_received, unit_price, std_price FROM purchase_order_items")
    if po.empty:
        return po
    for c in ("order_date", "promised_date", "received_date"):
        po[c] = pd.to_datetime(po[c])
    po["lead"] = (po.received_date - po.order_date).dt.days
    po["late_days"] = (po.received_date - po.promised_date).dt.days.clip(lower=0)
    po["on_time"] = po.late_days == 0
    it = it.assign(full=it.qty_received >= it.qty_ordered - 1e-9, var=(it.unit_price - it.std_price) * it.qty_received,
                   base=it.std_price * it.qty_received)
    g = it.groupby("po_id").agg(ordered=("qty_ordered", "sum"), received=("qty_received", "sum"), full=("full", "all"),
                                var=("var", "sum"), base=("base", "sum")).reset_index()
    po = po.merge(g, on="po_id")
    po["otif"] = po.on_time & po.full
    return po

def _risk_from_score(score, open_delayed=0) -> str:
    if score is None or pd.isna(score):
        return "Unknown"
    r = "Low" if score >= 85 else "Medium" if score >= 72 else "High" if score >= 60 else "Critical"
    return "Medium" if r == "Low" and open_delayed else r

def supplier_stats() -> pd.DataFrame:
    sup = db.df("SELECT id AS supplier_id, name, country, category, default_lead_time_days FROM suppliers")
    po = _closed_pos()
    spend = db.df("SELECT p.supplier_id, SUM(i.qty_ordered*i.unit_price) AS spend FROM purchase_orders p JOIN purchase_order_items i ON i.po_id=p.id GROUP BY p.supplier_id")
    od = db.df("SELECT supplier_id, COUNT(*) AS open_delayed FROM purchase_orders WHERE status='delayed' GROUP BY supplier_id")
    prim = db.df("SELECT primary_supplier_id AS supplier_id, COUNT(*) AS primary_materials, SUM(is_critical) AS critical_materials FROM materials GROUP BY primary_supplier_id")
    if not po.empty:
        s = po.groupby("supplier_id").agg(closed_pos=("po_id", "count"), otd=("on_time", "mean"), avg_lead=("lead", "mean"),
                                          lead_std=("lead", "std"), otif=("otif", "mean"), ordered=("ordered", "sum"),
                                          received=("received", "sum"), var=("var", "sum"), base=("base", "sum")).reset_index()
        late = po[po.late_days > 0].groupby("supplier_id").late_days.mean().rename("avg_delay_when_late").reset_index()
        s = s.merge(late, on="supplier_id", how="left")
        s["fill_rate"] = s.received / s.ordered
        s["price_var"] = s["var"] / s.base
        s["lead_std"] = s.lead_std.fillna(0)
        s["lead_cv"] = s.lead_std / s.avg_lead
        s = s.drop(columns=["ordered", "received", "var", "base"])
    else:
        s = pd.DataFrame(columns=["supplier_id", "closed_pos", "otd", "avg_lead", "lead_std", "otif", "avg_delay_when_late", "fill_rate", "price_var", "lead_cv"])
    out = sup.merge(s, on="supplier_id", how="left").merge(spend, on="supplier_id", how="left").merge(od, on="supplier_id", how="left").merge(prim, on="supplier_id", how="left")
    out[["spend", "open_delayed", "primary_materials", "critical_materials"]] = out[["spend", "open_delayed", "primary_materials", "critical_materials"]].fillna(0)
    out["spend_share"] = out.spend / out.spend.sum() if out.spend.sum() else 0
    out["avg_delay_when_late"] = out.avg_delay_when_late.fillna(0)
    out["score"] = 100 * (0.45 * out.otd + 0.25 * out.fill_rate + 0.15 * (1 - out.lead_cv.clip(upper=1)) +
                          0.15 * (1 - (out.price_var.clip(lower=0) / 0.10).clip(upper=1)))
    out["risk"] = [_risk_from_score(a, b) for a, b in zip(out.score, out.open_delayed)]
    return out.round(4)

# ───────────────────────── shipments ─────────────────────────
def shipments_table() -> pd.DataFrame:
    s = db.df("""SELECT s.id AS shipment_id, s.tracking_number, s.carrier, s.status, s.origin, s.destination, s.eta_original, s.eta_current,
                        s.actual_delivery, s.last_event, s.current_location, s.lat, s.lon, p.po_number, p.id AS po_id, p.supplier_id, sp.name AS supplier
                 FROM shipments s JOIN purchase_orders p ON p.id=s.po_id JOIN suppliers sp ON sp.id=p.supplier_id""")
    items = db.df("SELECT i.po_id, m.name || ' (' || CAST(CAST(i.qty_ordered AS INTEGER) AS TEXT) || ' ' || m.unit || ')' AS label FROM purchase_order_items i JOIN materials m ON m.id=i.material_id")
    mat = items.groupby("po_id").label.apply(lambda x: "; ".join(x)).rename("materials").reset_index() if not items.empty else pd.DataFrame(columns=["po_id", "materials"])
    s = s.merge(mat, on="po_id", how="left")
    end = pd.to_datetime(s.actual_delivery.fillna(s.eta_current))
    s["delay_days"] = (end - pd.to_datetime(s.eta_original)).dt.days.fillna(0).astype(int)
    s["delayed"] = s.delay_days > 0
    s["active"] = s.status != "Delivered"
    return s

# ───────────────────────── inventory ─────────────────────────
def _inbound() -> pd.DataFrame:
    d = db.df("""SELECT it.material_id, it.qty_ordered-it.qty_received AS qty, p.po_number, p.promised_date, p.status, s.eta_current
                 FROM purchase_order_items it JOIN purchase_orders p ON p.id=it.po_id LEFT JOIN shipments s ON s.po_id=p.id
                 WHERE p.status IN ('open','confirmed','in_transit','delayed')""")
    d["arrival"] = pd.to_datetime(d.eta_current.fillna(d.promised_date))
    return d

def _classify(avail, daily, arrival_days, lead):
    """Return (risk, cover_days, gap_days). gap = days between stock-out and the next inbound arrival (or new-order lead time)."""
    if daily <= 0:
        return "None", None, None
    cover = avail / daily
    if cover <= 0:
        return "Critical", 0.0, (arrival_days if arrival_days is not None else lead)
    ref = arrival_days if arrival_days is not None else lead
    gap = ref - cover
    if arrival_days is not None and arrival_days <= cover:
        return "Low", cover, gap
    if cover <= 3 or gap > 5:
        return "Critical", cover, gap
    if gap > 2:
        return "High", cover, gap
    if gap > 0:
        return "Medium", cover, gap
    return "Low", cover, gap

def inventory_table() -> pd.DataFrame:
    t = pd.Timestamp(today())
    m = db.df("""SELECT m.id AS material_id, m.sku, m.name, m.category, m.unit, m.unit_cost, m.safety_stock, m.reorder_level AS static_reorder_level,
                        m.primary_supplier_id, m.is_critical, i.on_hand, i.allocated, i.location, i.last_counted_qty, i.last_counted_at
                 FROM materials m JOIN inventory i ON i.material_id=m.id ORDER BY m.id""")
    mv = db.df("SELECT material_id, movement_date, quantity FROM inventory_movements WHERE type='issue'")
    mv["age"] = (t - pd.to_datetime(mv.movement_date)).dt.days
    s = lambda mask: mv[mask].groupby("material_id").quantity.sum()
    m["daily_demand"] = m.material_id.map(s(mv.age <= 30) / 30).fillna(0)
    d90 = m.material_id.map(s(mv.age <= 90) / 90).fillna(0)
    l14, p14 = m.material_id.map(s(mv.age <= 14)).fillna(0), m.material_id.map(s((mv.age > 14) & (mv.age <= 28))).fillna(0)
    m["demand_trend_pct"] = np.where(p14 > 0, (l14 - p14) / p14, np.nan)
    issues60 = m.material_id.map(s(mv.age <= 60)).fillna(0)
    ss = supplier_stats().set_index("supplier_id")
    lead = [ss.avg_lead.get(sid) if pd.notna(ss.avg_lead.get(sid)) else ss.default_lead_time_days.get(sid, 14) for sid in m.primary_supplier_id]
    m["lead_time_days"] = np.round(lead, 1)
    m["supplier"] = m.primary_supplier_id.map(ss.name)
    inb = _inbound()
    g = inb.groupby("material_id").agg(inbound_qty=("qty", "sum"), arrival=("arrival", "min"), inbound_pos=("po_number", lambda x: ", ".join(x))).reset_index()
    m = m.merge(g, on="material_id", how="left")
    m["inbound_qty"] = m.inbound_qty.fillna(0)
    m["arrival_days"] = (m.arrival - t).dt.days
    m = m.drop(columns=["arrival"])
    m["available"] = m.on_hand - m.allocated
    res = [_classify(a, d, (None if pd.isna(ad) else int(ad)), l) for a, d, ad, l in zip(m.available, m.daily_demand, m.arrival_days, m.lead_time_days)]
    m["risk"] = [r[0] for r in res]
    m["cover_days"] = [r[1] for r in res]
    m["gap_days"] = [r[2] for r in res]
    m["reorder_point"] = np.ceil(m.daily_demand * m.lead_time_days + m.safety_stock)
    m["inventory_position"] = m.available + m.inbound_qty
    m["below_reorder_point"] = (m.inventory_position < m.reorder_point) & (m.daily_demand > 0)
    m["inventory_value"] = m.on_hand * m.unit_cost
    m["annual_consumption_value"] = m.material_id.map(lambda i: 0) + d90 * 365 * m.unit_cost
    m["turnover"] = m.annual_consumption_value / m.inventory_value.replace(0, np.nan)
    srt = m.sort_values("annual_consumption_value", ascending=False)
    share = srt.annual_consumption_value.cumsum() / max(srt.annual_consumption_value.sum(), 1e-9)
    m["abc"] = pd.Series(np.where(share <= .80, "A", np.where(share <= .95, "B", "C")), index=srt.index).reindex(m.index)
    m.loc[m.annual_consumption_value <= 0, "abc"] = "C"
    cov = m.cover_days.astype(float)
    m["slow_moving"] = ((issues60 == 0) & (m.on_hand > 0)) | (cov > 180)
    m["excess_flag"] = m.slow_moving | (cov > 90)
    target = m.daily_demand * (m.lead_time_days + 30) + m.safety_stock
    m["excess_qty"] = np.where(m.excess_flag, (m.on_hand - target).clip(lower=0), 0)
    m["excess_value"] = m.excess_qty * m.unit_cost
    m["critical"] = m.is_critical == 1
    return m.round(3)

def stockout_risk(horizon_days: int = 7, min_level: str = "Medium") -> list[dict]:
    inv = inventory_table()
    inv = inv[inv.risk.map(RISK_RANK) <= RISK_RANK.get(min_level, 2)].copy()
    inv["stockout_within_horizon"] = inv.cover_days.astype(float) <= horizon_days
    inv = inv.sort_values(by=["risk", "cover_days"], key=lambda c: c.map(RISK_RANK) if c.name == "risk" else c)
    return records(inv)

def demand_series(material_id: int, days: int = 90) -> pd.DataFrame:
    d = db.df("SELECT movement_date AS day, SUM(quantity) AS qty FROM inventory_movements WHERE material_id=:m AND type='issue' GROUP BY movement_date", m=int(material_id))
    idx = pd.date_range(end=pd.Timestamp(today()) - pd.Timedelta(days=1), periods=days)
    d["day"] = pd.to_datetime(d.day)
    return d.set_index("day").qty.reindex(idx, fill_value=0).rename_axis("day").reset_index()

def forecast_demand(material_id: int, horizon: int = 14) -> dict:
    s = demand_series(material_id, 90)
    if (s.qty > 0).sum() < 14:
        return {"available": False, "reason": "Fewer than 14 days of consumption history."}
    level = s.qty.ewm(span=10).mean().iloc[-1]
    slope = np.polyfit(np.arange(30), s.qty.tail(30).to_numpy(), 1)[0]
    f = [max(0.0, level + slope * (i + 1) * 0.5) for i in range(horizon)]
    return {"available": True, "method": "EWMA level + damped 30-day linear trend", "daily_forecast": [round(x, 1) for x in f],
            "total": round(sum(f), 1), "basis": "forecast"}

# ───────────────────────── production ─────────────────────────
def production_impact() -> dict:
    po = db.df("SELECT * FROM production_orders WHERE status IN ('planned','in_progress') ORDER BY due_date, id")
    bom = db.df("SELECT b.production_order_id, b.material_id, b.qty_required, m.name AS material, m.unit FROM production_bom b JOIN materials m ON m.id=b.material_id")
    inv = db.df("SELECT material_id, on_hand-allocated AS avail FROM inventory")
    avail = {int(a): float(b) for a, b in zip(inv.material_id, inv.avail)}
    inbound: dict[int, list] = {}
    for r in _inbound().sort_values("arrival").itertuples():
        inbound.setdefault(int(r.material_id), []).append([r.arrival.date(), float(r.qty), r.po_number])
    out, T = [], today()
    for o in po.itertuples():
        lines, delays, uncovered = [], [], False
        for b in bom[bom.production_order_id == o.id].itertuples():
            mid = int(b.material_id)
            take = min(max(avail.get(mid, 0), 0), b.qty_required)
            avail[mid] = avail.get(mid, 0) - take
            short = b.qty_required - take
            ready, need, src = None, short, []
            if short > 1e-9:
                for e in inbound.get(mid, []):
                    if e[1] <= 0:
                        continue
                    use = min(need, e[1]); e[1] -= use; need -= use; ready = e[0]; src.append(e[2])
                    if need <= 1e-9:
                        break
            delay = None
            if short <= 1e-9:
                status = "In stock"
            elif need > 1e-9:
                status, uncovered = "Not fully covered by open POs", True
            else:
                delay = (ready - date.fromisoformat(o.due_date)).days
                delays.append(max(delay, 0))
                status = "Covered by inbound – on time" if delay <= 0 else f"Delayed ~{delay} days"
            lines.append(dict(material=b.material, unit=b.unit, required=b.qty_required, from_stock=round(take, 1), shortfall=round(short, 1),
                              uncovered=round(max(need, 0), 1), inbound_ready=str(ready) if ready else None, inbound_po=", ".join(src), delay_days=delay, status=status))
        delay = max(delays) if delays else 0
        level = "Critical" if uncovered or delay >= 3 else "High" if delay > 0 else "Medium" if any(l["shortfall"] > 0 for l in lines) else "Low"
        out.append(dict(order_number=o.order_number, product=o.product_name, qty=o.qty, due_date=o.due_date, status=o.status,
                        order_value=o.order_value, est_delay_days=delay, material_uncovered=uncovered, risk=level,
                        value_at_risk=o.order_value if (delay > 0 or uncovered) else 0, lines=lines,
                        affected_materials=[l["material"] for l in lines if l["shortfall"] > 0]))
    return {"orders": out, "assumption": "Stock is allocated to orders in due-date order; inbound dates are current ETAs and may still slip.",
            "summary": {"orders": len(out), "at_risk": sum(1 for x in out if x["value_at_risk"] > 0),
                        "value_at_risk": sum(x["value_at_risk"] for x in out), "max_delay_days": max([x["est_delay_days"] for x in out], default=0)}}

# ───────────────────────── procurement ─────────────────────────
def _find_material(ref) -> dict | None:
    r = db.rows("SELECT * FROM materials WHERE CAST(id AS TEXT)=:r OR LOWER(sku)=LOWER(:r) OR LOWER(name)=LOWER(:r)", r=str(ref))
    if not r:
        r = db.rows("SELECT * FROM materials WHERE LOWER(name) LIKE :r OR LOWER(sku) LIKE :r", r=f"%{str(ref).lower()}%")
    return r[0] if r else None

def compare_suppliers(material_ref, quantity: float = 0) -> dict:
    mat = _find_material(material_ref)
    if not mat:
        return {"error": f"Material '{material_ref}' not found."}
    q = db.df("SELECT q.supplier_id, s.name AS supplier, s.country, q.unit_price, q.lead_time_days, q.moq FROM supplier_quotes q JOIN suppliers s ON s.id=q.supplier_id WHERE q.material_id=:m", m=mat["id"])
    st = supplier_stats().set_index("supplier_id")
    qty = quantity if quantity > 0 else float(q.moq.max())
    q["otd"], q["score"], q["risk"] = q.supplier_id.map(st.otd), q.supplier_id.map(st.score), q.supplier_id.map(st.risk)
    q["lead_std"] = q.supplier_id.map(st.lead_std)
    q["qty"] = np.maximum(qty, q.moq)
    q["total_cost"] = q.qty * q.unit_price
    n = lambda c: (c - c.min()) / (c.max() - c.min()) if c.max() > c.min() else c * 0
    sc = q.score.fillna(50)
    q["composite"] = 100 * (1 - (0.40 * n(q.unit_price) + 0.25 * n(q.lead_time_days) + 0.35 * (1 - n(sc) if sc.max() > sc.min() else 0 * sc)))
    q = q.sort_values("composite", ascending=False)
    best = q.iloc[0]
    why = f"{best.supplier}: best weighted score (price 40%, reliability 35%, lead time 25%) – unit price {best.unit_price:.2f}, quoted lead time {best.lead_time_days} d, on-time rate " + \
          (f"{best.otd:.0%}" if pd.notna(best.otd) else "n/a (no history)")
    return {"material": mat["name"], "sku": mat["sku"], "unit": mat["unit"], "quantity_basis": qty, "options": records(q),
            "recommended": best.supplier, "reasoning": why,
            "caveat": "Composite score is a decision aid, not a guarantee; weights are configurable in analytics.compare_suppliers."}

def replenishment_plan() -> list[dict]:
    inv = inventory_table()
    inv = inv[inv.below_reorder_point | inv.risk.isin(["Critical", "High"])]
    plan = []
    for r in inv.itertuples():
        target = r.reorder_point + r.daily_demand * 30
        need = max(0.0, target - r.inventory_position)
        cmp_ = compare_suppliers(r.material_id, need)
        if "error" in cmp_:
            continue
        best = next(o for o in cmp_["options"] if o["supplier"] == cmp_["recommended"])
        qty = float(math.ceil(max(need, best["moq"]) / 10) * 10)
        expedite = r.risk in ("Critical", "High") and r.inbound_qty > 0
        plan.append(dict(material_id=int(r.material_id), material=r.name, sku=r.sku, unit=r.unit, risk=r.risk, available=r.available,
                         inbound_qty=r.inbound_qty, reorder_point=r.reorder_point, suggested_qty=qty, supplier=best["supplier"],
                         unit_price=best["unit_price"], est_cost=round(qty * best["unit_price"], 2), supplier_lead_days=best["lead_time_days"],
                         action="Expedite inbound + place top-up order" if expedite else "Place purchase order",
                         basis="Target = reorder point + 30 days of demand; rounded up to MOQ/10s. Estimate based on 30-day average demand."))
    return sorted(plan, key=lambda x: RISK_RANK[x["risk"]])

# ───────────────────────── KPIs ─────────────────────────
def kpis() -> list[dict]:
    inv, sup, sh, pr, po = inventory_table(), supplier_stats(), shipments_table(), production_impact(), _closed_pos()
    out = []
    def add(key, label, value, unit, basis, note="", reason=None):
        out.append(dict(key=key, label=label, value=None if reason else (None if value is None or (isinstance(value, float) and math.isnan(value)) else round(float(value), 4)),
                        unit=unit, basis=basis, note=note, unavailable_reason=reason))
    inv_val, ann = inv.inventory_value.sum(), inv.annual_consumption_value.sum()
    add("inventory_value", "Total inventory value", inv_val, "USD", "actual", "On-hand × standard cost")
    add("turnover", "Inventory turnover", ann / inv_val if inv_val else None, "x / year", "estimate", "90-day issues annualised ÷ current inventory value (no historical balances stored)")
    add("doh", "Days of inventory on hand", inv_val / (ann / 365) if ann else None, "days", "estimate", "Inventory value ÷ average daily consumption value")
    c = inv.dropna(subset=["last_counted_qty"])
    if c.empty:
        add("accuracy", "Inventory accuracy", None, "%", "actual", reason="No physical-count data.")
    else:
        add("accuracy", "Inventory accuracy", ((c.last_counted_qty - c.on_hand).abs() <= 0.02 * c.on_hand).mean() * 100, "%", "actual", f"{len(c)} counted SKUs; within ±2% of system")
    add("stockout_risk", "Materials at Critical/High stock-out risk", int(inv.risk.isin(["Critical", "High"]).sum()), "materials", "estimate", "Cover vs inbound arrival / lead time")
    add("below_rop", "Materials below reorder point", int(inv.below_reorder_point.sum()), "materials", "estimate", "Position (stock + inbound) < demand × lead time + safety")
    if po.empty:
        for k in ("otd", "lead", "lead_var", "fill", "otif", "cost_var"):
            add(k, k, None, "", "actual", reason="No received purchase orders.")
    else:
        add("otd", "Supplier on-time delivery", po.on_time.mean() * 100, "%", "actual", f"{len(po)} received POs; received ≤ promised date")
        add("lead", "Average supplier lead time", po.lead.mean(), "days", "actual", "Order date → received date")
        add("lead_var", "Lead-time variability (std dev)", po.lead.std(), "days", "actual", "Pooled across suppliers; see Suppliers for per supplier")
        add("fill", "Order fulfilment rate", po.received.sum() / po.ordered.sum() * 100, "%", "actual", "Quantity received ÷ quantity ordered")
        add("otif", "OTIF (on-time, in-full)", po.otif.mean() * 100, "%", "actual", "PO on time AND every line fully received")
        add("cost_var", "Procurement cost variance", po["var"].sum() / po.base.sum() * 100, "%", "actual", "(Paid − standard) ÷ standard, on received quantity")
    add("perfect", "Perfect order rate", None, "%", "actual", reason="Needs damage/documentation-accuracy data that is not captured.")
    add("excess", "Excess inventory value", inv.loc[inv.excess_flag, "excess_value"].sum(), "USD", "estimate", "Stock above lead-time + 30-day target on over-covered items")
    add("slow", "Slow-moving inventory value", inv.loc[inv.slow_moving, "inventory_value"].sum(), "USD", "estimate", "No issues in 60 days or > 180 days cover")
    add("ship_delay", "Shipment delay rate", sh.delayed.mean() * 100 if len(sh) else None, "%", "actual", "Shipments whose current/actual date is later than original ETA")
    done = db.df("SELECT due_date, completed_date FROM production_orders WHERE status='completed' AND completed_date IS NOT NULL")
    if done.empty:
        add("sched", "Production schedule adherence", None, "%", "actual", reason="No completed production orders.")
    else:
        add("sched", "Production schedule adherence", (pd.to_datetime(done.completed_date) <= pd.to_datetime(done.due_date)).mean() * 100, "%", "actual", f"{len(done)} completed orders finished on/before due date")
    return out

# ───────────────────────── overview / health ─────────────────────────
def overview() -> dict:
    inv, sh, pr = inventory_table(), shipments_table(), production_impact()
    active = sh[sh.active]
    po_open = db.rows("SELECT COUNT(*) AS n FROM purchase_orders WHERE status IN ('open','confirmed','in_transit','delayed')")[0]["n"]
    crit, high = int((inv.risk == "Critical").sum()), int((inv.risk == "High").sum())
    delay_rate = active.delayed.mean() if len(active) else 0
    score = 100 - 12 * crit - 6 * high - 25 * delay_rate - 4 * sum(1 for o in pr["orders"] if o["risk"] == "Critical")
    return {"inventory_value": float(inv.inventory_value.sum()), "active_pos": int(po_open), "in_transit": int(len(active)),
            "delayed_shipments": int(active.delayed.sum()), "critical_shortages": crit, "high_shortages": high,
            "supplier_high_risk": int(supplier_stats().risk.isin(["High", "Critical"]).sum()),
            "production_value_at_risk": float(pr["summary"]["value_at_risk"]),
            "health_score": round(max(0.0, min(100.0, score)), 1),
            "health_note": "Heuristic: 100 − 12/critical shortage − 6/high shortage − 25×active-shipment delay rate − 4/critical production order"}

# ───────────────────────── rule-based risk detection ─────────────────────────
def detect_risks() -> dict:
    inv, sh, pr, sup = inventory_table(), shipments_table(), production_impact(), supplier_stats()
    plan = {p["material"]: p for p in replenishment_plan()}
    found = []
    for r in inv[inv.risk.isin(["Critical", "High", "Medium"])].itertuples():
        affected = [o["order_number"] for o in pr["orders"] if r.name in o["affected_materials"]]
        p = plan.get(r.name)
        rec = (f"{p['action']}: {p['suggested_qty']:,.0f} {r.unit} from {p['supplier']} (≈ {p['est_cost']:,.0f} USD)." if p else "Review demand and supplier ETA.")
        found.append(dict(sev=r.risk, cat="Inventory", title=f"{r.name}: stock-out risk",
                          exp=f"{r.available:,.0f} {r.unit} available, ~{r.cover_days:.1f} days of cover at {r.daily_demand:,.1f}/day; " +
                              (f"next inbound {r.inbound_qty:,.0f} in {int(r.arrival_days)} days." if pd.notna(r.arrival_days) else "no inbound order."),
                          aff=affected, ev={"available": r.available, "daily_demand": r.daily_demand, "cover_days": r.cover_days, "arrival_days": None if pd.isna(r.arrival_days) else r.arrival_days, "source": "inventory + purchase_orders"}, rec=rec))
    for r in sh[sh.active & (sh.delay_days >= 2)].itertuples():
        found.append(dict(sev="Critical" if r.delay_days >= 7 else "High", cat="Shipment", title=f"{r.po_number}: shipment delayed {r.delay_days} days",
                          exp=f"{r.supplier} shipment {r.tracking_number} now due {r.eta_current} (was {r.eta_original}). Last event: {r.last_event}.",
                          aff=[r.materials], ev={"tracking": r.tracking_number, "eta_original": r.eta_original, "eta_current": r.eta_current, "source": "shipments"},
                          rec=f"Confirm revised ETA with {r.supplier}; consider air freight or an alternate supplier for the affected material."))
    for o in pr["orders"]:
        if o["value_at_risk"] > 0:
            found.append(dict(sev=o["risk"], cat="Production", title=f"{o['order_number']}: production at risk",
                              exp=f"{o['product']} due {o['due_date']}: estimated delay {o['est_delay_days']} days" + ("; some materials not covered by open POs." if o["material_uncovered"] else "."),
                              aff=o["affected_materials"], ev={"value_at_risk": o["value_at_risk"], "source": "production_orders + BOM + inbound ETAs"},
                              rec="Re-sequence orders, split the batch, or expedite the constraining material."))
    for r in sup[sup.risk.isin(["High", "Critical"]) & (sup.primary_materials > 0)].itertuples():
        found.append(dict(sev="Medium", cat="Supplier", title=f"{r.name}: reliability risk",
                          exp=f"Score {r.score:.0f}/100, on-time {r.otd:.0%}, lead-time σ {r.lead_std:.1f} d; primary source for {int(r.primary_materials)} materials ({int(r.critical_materials)} critical).",
                          aff=[], ev={"score": r.score, "otd": r.otd, "source": "purchase_orders history"}, rec="Qualify a backup supplier and add safety stock for critical items."))
    ex = inv[inv.excess_flag & (inv.excess_value > 500)]
    for r in ex.itertuples():
        found.append(dict(sev="Low", cat="Inventory", title=f"{r.name}: excess / slow-moving stock", exp=f"≈ {r.excess_value:,.0f} USD above target.", aff=[],
                          ev={"excess_value": r.excess_value, "source": "inventory"}, rec="Pause replenishment; consider returns or reallocation."))
    t, titles = db.now(), set()
    for f in found:
        titles.add(f["title"])
        ex_ = db.rows("SELECT id FROM risk_alerts WHERE title=:t AND status='open'", t=f["title"])
        vals = dict(s=f["sev"], c=f["cat"], t=f["title"], e=f["exp"], a=json.dumps(f["aff"]), ev=json.dumps(f["ev"], default=str), r=f["rec"], n=t)
        if ex_:
            db.execute("UPDATE risk_alerts SET severity=:s, explanation=:e, affected=:a, evidence=:ev, recommendation=:r, updated_at=:n WHERE id=:i", i=ex_[0]["id"], **{k: v for k, v in vals.items() if k not in ("c", "t")})
        else:
            db.execute("INSERT INTO risk_alerts (severity, category, title, explanation, affected, evidence, recommendation, status, source, created_at, updated_at) VALUES (:s,:c,:t,:e,:a,:ev,:r,'open','rules',:n,:n)", **vals)
    cleared = 0
    for a in db.rows("SELECT id, title FROM risk_alerts WHERE status='open' AND source='rules'"):
        if a["title"] not in titles:
            db.execute("UPDATE risk_alerts SET status='cleared', updated_at=:n WHERE id=:i", n=t, i=a["id"]); cleared += 1
    return {"detected": len(found), "cleared": cleared}

def alerts(status: str = "open") -> list[dict]:
    d = db.df("SELECT * FROM risk_alerts WHERE (:s='' OR status=:s) ORDER BY id DESC", s=status)
    if d.empty:
        return []
    d["rank"] = d.severity.map(RISK_RANK)
    return records(d.sort_values(["rank", "id"], ascending=[True, False]).drop(columns="rank"))
