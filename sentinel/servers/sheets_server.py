"""MCP server: Google Sheets. Live when GOOGLE_SERVICE_ACCOUNT_FILE + GOOGLE_SHEET_ID are valid; otherwise read-only demo data (clearly flagged)."""
from __future__ import annotations
from pathlib import Path
from .. import analytics as A, db
from ..config import env
from ..schemas import InventoryUpdate
from .common import err, make, ok, require_approval

mcp = make("sentinel-sheets", "sheets")

def _live() -> bool:
    f = env("GOOGLE_SERVICE_ACCOUNT_FILE")
    return bool(env("GOOGLE_SHEET_ID") and f and Path(f).exists())

def _book():
    import gspread
    return gspread.service_account(filename=env("GOOGLE_SERVICE_ACCOUNT_FILE")).open_by_key(env("GOOGLE_SHEET_ID"))

def _read(ws: str):
    try:
        import gspread
        return True, _book().worksheet(ws).get_all_records()
    except Exception as e:
        name = type(e).__name__
        msg = str(e)
        if "429" in msg or "Quota" in msg:
            return False, "Google Sheets rate limit reached – retry in a minute."
        if "403" in msg or "PERMISSION" in msg.upper():
            return False, "Permission denied – share the sheet with the service-account e-mail."
        if name == "WorksheetNotFound":
            return False, f"Worksheet '{ws}' not found in the spreadsheet."
        return False, f"{name}: {msg[:200]}"

@mcp.tool()
def sheets_status() -> dict:
    """Health check for Google Sheets connectivity."""
    if not _live():
        db.set_integration("google_sheets", "demo", "demo", "No credentials – read-only demo data")
        return ok({"detail": "No credentials configured – demo mode (read-only, data from local database)."}, mode="demo", source="demo adapter")
    try:
        t = _book().title
        db.set_integration("google_sheets", "live", "connected", f"Spreadsheet '{t}'")
        return ok({"detail": f"Connected to spreadsheet '{t}'"}, mode="live", source="Google Sheets")
    except Exception as e:
        db.set_integration("google_sheets", "live", "error", str(e))
        return err(f"Cannot open spreadsheet: {str(e)[:200]}")

def _demo(sql: str):
    return ok(A.records(db.df(sql)), mode="demo", source="demo adapter (local database)", note="Google Sheets not configured – demo data")

@mcp.tool()
def sheets_get_inventory() -> dict:
    """Inventory records from the 'Inventory' worksheet (columns: sku, name, on_hand, unit …)."""
    if not _live():
        return _demo("SELECT m.sku, m.name, m.unit, i.on_hand, m.reorder_level, m.safety_stock FROM materials m JOIN inventory i ON i.material_id=m.id")
    good, d = _read("Inventory")
    return ok(d, "live", "Google Sheets") if good else err(d, "live")

@mcp.tool()
def sheets_get_suppliers() -> dict:
    """Supplier information from the 'Suppliers' worksheet."""
    if not _live():
        return _demo("SELECT id, name, country, category, contact_name, payment_terms, default_lead_time_days FROM suppliers")
    good, d = _read("Suppliers")
    return ok(d, "live", "Google Sheets") if good else err(d, "live")

@mcp.tool()
def sheets_get_purchase_orders() -> dict:
    """Purchase orders and delivery schedules from the 'PurchaseOrders' worksheet."""
    if not _live():
        return _demo("SELECT p.po_number, s.name AS supplier, p.order_date, p.promised_date, p.status FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id WHERE p.status<>'received' ORDER BY p.promised_date")
    good, d = _read("PurchaseOrders")
    return ok(d, "live", "Google Sheets") if good else err(d, "live")

@mcp.tool()
def sheets_update_inventory_record(sku: str, new_on_hand: float, reason: str, approval_id: int) -> dict:
    """CONTROLLED WRITE: update on_hand for one SKU in the Inventory worksheet. Needs an approved 'update_inventory' action. Verified by re-reading the cell."""
    try:
        req = InventoryUpdate(sku=sku, new_on_hand=new_on_hand, reason=reason, approval_id=approval_id)
    except Exception as e:
        return err(f"Invalid request: {e}", "live")
    if not _live():
        return err("Google Sheets is not connected – demo mode is read-only. No change was made.", "demo")
    bad = require_approval(req.approval_id, "update_inventory", {"sku": req.sku, "new_on_hand": req.new_on_hand})
    if bad:
        return err(bad, "live")
    try:
        ws = _book().worksheet("Inventory")
        head = ws.row_values(1)
        col, cell = head.index("on_hand") + 1, ws.find(req.sku, in_column=head.index("sku") + 1)
        if cell is None:
            return err(f"SKU {req.sku} not found in sheet.", "live")
        ws.update_cell(cell.row, col, req.new_on_hand)
        verified = float(ws.cell(cell.row, col).value) == req.new_on_hand
        res = {"sku": req.sku, "row": cell.row, "new": req.new_on_hand, "verified": verified}
        db.set_action(req.approval_id, status="executed" if verified else "failed", result=res)
        return ok(res, "live", "Google Sheets") if verified else err("Write could not be verified.", "live", **res)
    except Exception as e:
        return err(f"Sheets write failed: {str(e)[:200]}", "live")

if __name__ == "__main__":
    db.init_db()
    mcp.run(transport="streamable-http")
