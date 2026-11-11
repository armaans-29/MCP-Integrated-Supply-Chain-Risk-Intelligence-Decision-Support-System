"""Push the demo data to your Google Sheet (worksheets: Inventory, Suppliers, PurchaseOrders).
Usage: python scripts/seed_google_sheet.py   (needs GOOGLE_SERVICE_ACCOUNT_FILE + GOOGLE_SHEET_ID in .env)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gspread
from sentinel import db
from sentinel.config import env

db.init_db()
book = gspread.service_account(filename=env("GOOGLE_SERVICE_ACCOUNT_FILE")).open_by_key(env("GOOGLE_SHEET_ID"))
sets = {
    "Inventory": "SELECT m.sku, m.name, m.unit, i.on_hand, m.reorder_level, m.safety_stock FROM materials m JOIN inventory i ON i.material_id=m.id ORDER BY m.id",
    "Suppliers": "SELECT id, name, country, category, contact_name, payment_terms, default_lead_time_days FROM suppliers ORDER BY id",
    "PurchaseOrders": "SELECT p.po_number, s.name AS supplier, p.order_date, p.promised_date, p.status FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id WHERE p.status<>'received' ORDER BY p.promised_date",
}
for name, sql in sets.items():
    d = db.df(sql).fillna("")
    try:
        ws = book.worksheet(name); ws.clear()
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(name, rows=len(d) + 10, cols=len(d.columns) + 2)
    ws.update([d.columns.tolist()] + d.astype(object).values.tolist())
    print(f"✔ {name}: {len(d)} rows")
