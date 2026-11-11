"""Realistic, relationally-consistent demo dataset (dates are relative to today)."""
from __future__ import annotations
import random
from datetime import date, timedelta
from . import db

# id, name, country, category, contact, terms, on-time probability, mean lead (d), lead std
SUP = [
    (1, "Alumex Metals", "India", "Metals", "Rohit Sharma", "Net 30", .92, 14, 2),
    (2, "Cuprotech Industries", "India", "Metals & Wire", "Anita Rao", "Net 45", .88, 10, 3),
    (3, "FastenPro Ltd", "China", "Fasteners & Rubber", "Li Wei", "Net 60", .95, 21, 3),
    (4, "ChemCore Polymers", "Germany", "Chemicals", "Jonas Keller", "Net 30", .70, 18, 6),
    (5, "PackRight Solutions", "India", "Packaging", "Meera Nair", "Net 30", .96, 7, 1),
    (6, "VoltCell Asia", "China", "Electronics", "Chen Hao", "Net 45", .78, 28, 7),
    (7, "ChipSource Taiwan", "Taiwan", "Electronics", "Wen Lin", "Net 60", .65, 35, 9),
    (8, "Sahyadri Fasteners", "India", "Fasteners & Rubber", "Pradeep Joshi", "Net 30", .90, 12, 2),
    (9, "BioPoly Resins", "India", "Chemicals", "Kavita Menon", "Net 30", .85, 20, 4),
    (10, "GreenPack Industries", "India", "Packaging", "Sunil Das", "Net 30", .93, 6, 1),
]
# id, sku, name, category, unit, cost, daily demand, cover days, primary supplier, critical
MAT = [
    (1, "RM-ALU-002", "Aluminium Sheet 2mm", "Metals", "kg", 4.20, 120, 6, 1, 1),
    (2, "RM-CPR-015", "Copper Wire 1.5mm", "Metals", "kg", 9.80, 60, 25, 2, 0),
    (3, "CP-BLT-M08", "Steel Bolts M8", "Fasteners", "pcs", 0.12, 2000, 40, 3, 0),
    (4, "RM-RES-PX40", "Polymer Resin PX-40", "Chemicals", "kg", 3.10, 90, 4, 4, 1),
    (5, "PK-CTN-L", "Packaging Carton L", "Packaging", "pcs", 0.65, 400, 55, 5, 0),
    (6, "EL-CEL-18650", "Lithium Cells 18650", "Electronics", "pcs", 2.40, 500, 9, 6, 1),
    (7, "EL-PCB-V3", "PCB Board v3", "Electronics", "pcs", 6.50, 150, 14, 6, 0),
    (8, "CP-GSK-12", "Rubber Gasket 12mm", "Rubber", "pcs", 0.35, 800, 120, 3, 0),
    (9, "CH-LUB-5L", "Lubricant Oil 5L", "Chemicals", "l", 2.10, 25, 30, 4, 0),
    (10, "RM-SSR-010", "Stainless Steel Rod", "Metals", "kg", 5.60, 45, 18, 1, 0),
    (11, "EL-MCU-32", "Control Chip MCU", "Electronics", "pcs", 3.90, 200, 2.5, 7, 1),
    (12, "RM-FOM-IN", "Foam Insulation", "Insulation", "m2", 1.80, 0, 0, 5, 0),
]
QUOTES = {1: [(1, 1.0), (2, 1.06)], 2: [(2, 1.0), (1, 1.04)], 3: [(3, 1.0), (8, 1.12)], 4: [(4, 1.0), (9, .97)],
          5: [(5, 1.0), (10, 1.05)], 6: [(6, 1.0), (7, 1.08)], 7: [(6, 1.0), (7, 1.10)], 8: [(3, 1.0), (8, .95)],
          9: [(4, 1.0), (9, 1.03)], 10: [(1, 1.0), (2, 1.10)], 11: [(7, 1.0), (6, 1.12)], 12: [(5, 1.0), (10, .98)]}
# po_number, supplier, material, qty, order offset, promised offset, status, shipment tuple
OPEN_POS = [
    ("PO-2611-001", 4, 4, 1500, -16, 3, "delayed", (3, 9, "Delayed", "Customs hold at port", "Mumbai (Nhava Sheva)", 18.95, 72.95, "Hamburg, DE", "Maersk")),
    ("PO-2611-002", 7, 11, 3000, -28, 5, "delayed", (5, 12, "Delayed", "Vessel rerouted – typhoon advisory", "Kaohsiung", 22.62, 120.30, "Kaohsiung, TW", "Evergreen")),
    ("PO-2611-003", 6, 6, 8000, -20, 8, "in_transit", (8, 8, "In transit", "Departed Shenzhen", "Shenzhen", 22.54, 114.06, "Shenzhen, CN", "DHL")),
    ("PO-2611-004", 1, 1, 2500, -9, 4, "in_transit", (4, 4, "In transit", "Passed Nagpur hub", "Nagpur", 21.14, 79.08, "Raipur, IN", "BlueDart")),
    ("PO-2611-005", 2, 2, 800, -3, 10, "confirmed", None),
    ("PO-2611-006", 6, 7, 2000, -15, 9, "in_transit", (9, 10, "In transit", "Port congestion Shenzhen", "Shenzhen", 22.54, 114.06, "Shenzhen, CN", "DHL")),
]
# number, product, qty, due offset, value, BOM [(material, qty)]
PROD = [
    ("PRD-1001", "Motor Controller MC-5", 300, 5, 45000, [(11, 300), (7, 300), (1, 450)]),
    ("PRD-1002", "Battery Pack BP-200", 400, 6, 88000, [(6, 8000), (3, 1600)]),
    ("PRD-1003", "Polymer Housing PH-12", 1000, 4, 32000, [(4, 2000), (9, 50)]),
    ("PRD-1004", "Motor Controller MC-5", 600, 9, 90000, [(11, 600), (7, 600), (1, 900)]),
    ("PRD-1005", "Cable Assembly CA-9", 800, 14, 28000, [(2, 1200), (5, 800)]),
    ("PRD-1006", "Mounting Kit MK-3", 2000, 20, 12000, [(3, 8000), (8, 2000), (10, 300)]),
]

def seed_demo() -> None:
    rng = random.Random(42)
    today = date.today()
    iso = lambda d: d.isoformat()
    D = lambda n: iso(today + timedelta(days=n))
    sup_info = {s[0]: s for s in SUP}
    mat_info = {m[0]: m for m in MAT}
    mats_of = {s[0]: [m for m, q in QUOTES.items() if any(x[0] == s[0] for x in q)] for s in SUP}
    mult = {(m, s): k for m, q in QUOTES.items() for s, k in q}

    suppliers = [dict(id=s[0], name=s[1], country=s[2], category=s[3], contact_name=s[4], payment_terms=s[5],
                      default_lead_time_days=s[7], status="active") for s in SUP]
    materials, inventory, quotes = [], [], []
    for (mid, sku, name, cat, unit, cost, daily, cover, sup, crit) in MAT:
        lead = sup_info[sup][7]
        safety = round(daily * (4 if crit else 3))
        materials.append(dict(id=mid, sku=sku, name=name, category=cat, unit=unit, unit_cost=cost, safety_stock=safety,
                              reorder_level=round(daily * lead + safety), primary_supplier_id=sup, is_critical=crit))
        on_hand = 3000 if mid == 12 else round(daily * cover)
        counted = on_hand + (0 if mid % 2 else rng.choice([-12, 5, 30])) if mid <= 7 else None
        inventory.append(dict(material_id=mid, on_hand=on_hand, allocated=0, location=f"WH-{'A' if mid % 2 else 'B'}-{mid:02d}",
                              last_counted_qty=counted, last_counted_at=D(-10) if counted is not None else None, updated_at=db.now()))
        for k, (sid, m) in enumerate(QUOTES[mid]):
            s = sup_info[sid]
            quotes.append(dict(material_id=mid, supplier_id=sid, unit_price=round(cost * m, 3),
                               lead_time_days=max(3, s[7] + rng.randint(-1, 2)), moq=max(10, round(max(daily, 20) * 7, -1)),
                               valid_until=D(60)))

    pos, items, ships, moves = [], [], [], []
    poid = iid = shid = 0
    # ── closed (received) purchase orders → supplier history
    for s in SUP:
        sid, p_on, lead, std = s[0], s[6], s[7], s[8]
        for _ in range(9):
            poid += 1
            od = today - timedelta(days=lead + 45 + rng.randint(0, 140))
            promised = od + timedelta(days=lead)
            if rng.random() < p_on:
                actual = lead - rng.randint(0, max(1, int(std)))
            else:
                actual = lead + rng.randint(1, 3 + int(std))
            received = od + timedelta(days=max(2, actual))
            pos.append(dict(id=poid, po_number=f"PO-{od:%y%m}-{poid:03d}", supplier_id=sid, order_date=iso(od), promised_date=iso(promised),
                            received_date=iso(received), status="received", currency="USD"))
            for mid in rng.sample(mats_of[sid], min(len(mats_of[sid]), rng.choice([1, 2]))):
                iid += 1
                qty = max(50, round(max(mat_info[mid][6], 30) * rng.randint(10, 25), -1))
                got = qty if rng.random() < .85 else round(qty * rng.uniform(.85, .98))
                std_p = mat_info[mid][5]
                items.append(dict(id=iid, po_id=poid, material_id=mid, qty_ordered=qty, qty_received=got,
                                  unit_price=round(std_p * mult[(mid, sid)] * rng.uniform(.98, 1.05), 3), std_price=std_p))
                moves.append(dict(material_id=mid, movement_date=iso(received), type="receipt", quantity=got, reference=pos[-1]["po_number"]))
            if poid % 5 == 0 and received < today:
                shid += 1
                ships.append(dict(id=shid, po_id=poid, carrier=rng.choice(["DHL", "Maersk", "BlueDart"]), tracking_number=f"TRK{poid:05d}",
                                  origin=sup_info[sid][2], destination="Plant – Jharkhand", status="Delivered", eta_original=iso(promised),
                                  eta_current=iso(received), actual_delivery=iso(received), last_event="Delivered", last_event_at=iso(received),
                                  current_location="Plant", lat=None, lon=None))
    # ── open purchase orders + live shipments
    for (num, sid, mid, qty, oo, po_, status, sh) in OPEN_POS:
        poid += 1; iid += 1
        pos.append(dict(id=poid, po_number=num, supplier_id=sid, order_date=D(oo), promised_date=D(po_), received_date=None, status=status, currency="USD"))
        items.append(dict(id=iid, po_id=poid, material_id=mid, qty_ordered=qty, qty_received=0,
                          unit_price=round(mat_info[mid][5] * mult[(mid, sid)], 3), std_price=mat_info[mid][5]))
        if sh:
            shid += 1
            ships.append(dict(id=shid, po_id=poid, carrier=sh[8], tracking_number=f"TRK9{poid:04d}", origin=sh[7], destination="Plant – Jharkhand",
                              status=sh[2], eta_original=D(sh[0]), eta_current=D(sh[1]), actual_delivery=None, last_event=sh[3],
                              last_event_at=iso(today - timedelta(days=1)), current_location=sh[4], lat=sh[5], lon=sh[6]))
    # ── daily consumption (90 days)
    for (mid, *_rest) in MAT:
        daily = mat_info[mid][6]
        if mid == 12:
            for off in (80, 75, 70):
                moves.append(dict(material_id=mid, movement_date=iso(today - timedelta(days=off)), type="issue", quantity=200, reference="WO-hist"))
            continue
        for off in range(1, 91):
            moves.append(dict(material_id=mid, movement_date=iso(today - timedelta(days=off)), type="issue",
                              quantity=round(daily * rng.uniform(.65, 1.35)), reference="WO-daily"))
    # ── production orders
    prod, bom = [], []
    pid = bid = 0
    for (num, name, qty, off, val, lines) in PROD:
        pid += 1
        prod.append(dict(id=pid, order_number=num, product_name=name, qty=qty, due_date=D(off), status="in_progress" if off <= 6 else "planned",
                         order_value=val, completed_date=None))
        for mid, q in lines:
            bid += 1
            bom.append(dict(id=bid, production_order_id=pid, material_id=mid, qty_required=q))
    for k in range(8):
        pid += 1
        due = today - timedelta(days=rng.randint(10, 120))
        prod.append(dict(id=pid, order_number=f"PRD-0{900 + k}", product_name=rng.choice(["Cable Assembly CA-9", "Mounting Kit MK-3", "Battery Pack BP-200"]),
                         qty=rng.randint(200, 900), due_date=iso(due), status="completed", order_value=rng.randint(10, 60) * 1000,
                         completed_date=iso(due + timedelta(days=rng.choice([-2, 0, 0, 0, 1, 3, 5])))))

    with db.engine().begin() as c:
        for t, r in [("suppliers", suppliers), ("materials", materials), ("inventory", inventory), ("supplier_quotes", [dict(id=i + 1, **q) for i, q in enumerate(quotes)]),
                     ("purchase_orders", pos), ("purchase_order_items", items), ("shipments", ships), ("production_orders", prod),
                     ("production_bom", bom), ("inventory_movements", moves)]:
            db.bulk_insert(c, t, r)
    from .analytics import detect_risks
    detect_risks()
