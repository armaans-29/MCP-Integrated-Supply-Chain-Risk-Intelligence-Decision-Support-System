# 🛰️ SupplyChain Sentinel AI
MCP-powered supply-chain control tower: it connects inventory, suppliers, shipments, production and weather through **real MCP servers**, and a **LangGraph agent** investigates disruptions *before* they become expensive – with human approval before any external action.

## Quick start (2 minutes, no keys needed)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # optional – demo mode works without edits
streamlit run app.py                                   # opens http://localhost:8501
```
The app creates a realistic SQLite demo database and **starts the five MCP servers itself**. Prefer separate processes? `python run_servers.py`, then `streamlit run app.py`.
Docker: `cp .env.example .env && docker compose up --build`.

Try these in **🤖 AI Agent**: *Which materials are at risk of running out this week?* · *A supplier has delayed my raw material delivery. What will happen?* · *Which supplier should I choose for polymer resin?* · *Notify my procurement team about critical shortages.*

## Architecture
```
Streamlit UI ─┬─ Manual mode  → analytics.py (same formulas)  ──┐
              └─ AI Agent mode → LangGraph agent → MCP client ──┤ (Streamable HTTP, tool discovery)
                                                                ▼
   data :8101   sheets :8102   slack :8103   logistics :8104   weather :8105     ← 5 independent FastMCP servers
   Supabase/    Google Sheets  Slack Web API  Supplier/carrier   Open-Meteo
   PostgreSQL                                  API adapter
```
| MCP server | Tools |
|---|---|
| data | `get_inventory_levels` `get_supplier_details` `get_supplier_performance` `get_purchase_orders` `get_production_orders` `calculate_stockout_risk` `calculate_production_impact` `compare_supplier_options` `generate_replenishment_plan` `get_kpis` `get_risk_alerts` `run_risk_detection` `update_inventory_record`* `data_status` |
| sheets | `sheets_get_inventory` `sheets_get_suppliers` `sheets_get_purchase_orders` `sheets_update_inventory_record`* `sheets_status` |
| slack | `send_slack_notification`* `slack_status` |
| logistics | `get_shipment_status` `get_supplier_delivery_status` `logistics_status` |
| weather | `get_weather_conditions` `weather_status` |

\* write tools – require an **approved** action from an approver/admin; the server re-checks the approval and that the payload is exactly what was approved.

**Agent graph:** `plan → gather → compose → (notify?) propose → ⏸ human approval → send → finalize`. Tools are discovered dynamically; an optional LLM (Groq/Ollama) may add read-only tool calls and writes the summary, but *every number comes from tool results*.

## Honest by design
- **Modes**: `live` (real service answered) · `demo` (clearly flagged sample data, writes disabled) · `disconnected` (no credentials/service down). Integration page measures this live.
- Nothing is reported as done unless the service confirms it (Slack must return `ok` + `ts`). Failures surface as "Not sent".
- KPIs label **actual** vs **estimate** vs **forecast**; KPIs without data (e.g. Perfect Order Rate) show *n/a + reason*.
- No raw-SQL tool, Pydantic-validated writes, audit log (`action_history`), role checks, secrets only via `.env`.

## Connecting real services
| Service | Steps |
|---|---|
| Supabase | `DATABASE_URL=postgresql+psycopg2://…` (Project → Database → URI). Tables + demo data are created on first start, or run `sentinel/schema.sql`. |
| Google Sheets | Service-account JSON → `GOOGLE_SERVICE_ACCOUNT_FILE`; share sheet with its e-mail; `GOOGLE_SHEET_ID`; push template with `python scripts/seed_google_sheet.py` (worksheets *Inventory* `sku,name,unit,on_hand…`, *Suppliers*, *PurchaseOrders*). |
| Slack | App with `chat:write` → `SLACK_BOT_TOKEN`; invite the bot; `SLACK_DEFAULT_CHANNEL`. |
| Logistics API | `LOGISTICS_API_URL` (+`LOGISTICS_API_KEY`, sent as Bearer). Expects `GET /shipments → {"shipments":[{"tracking_number","status","eta","last_event","location","lat","lon"}]}`; records are matched to your POs by `tracking_number`. |
| Weather | Open-Meteo, no key. `WEATHER_MODE=demo` for offline (synthetic, flagged). |
| LLM | `GROQ_API_KEY` (or `OLLAMA_MODEL` + `pip install langchain-ollama`). Optional. |

## Key formulas (see `sentinel/analytics.py`)
Days of cover = (on-hand − allocated) ÷ 30-day avg daily demand · Reorder point = demand × lead time + safety stock · Stock-out risk compares cover with next inbound ETA (or lead time) · Turnover = 90-day issue value annualised ÷ inventory value · OTD/OTIF/fill rate/lead-time σ/price variance from received POs · Supplier score = 45% on-time + 25% fill + 15% lead stability + 15% price discipline (heuristic) · Production impact allocates stock by due date, then inbound ETAs.

## Tests
`pytest -q` starts the MCP servers on test ports and covers discovery, tool calls, approval enforcement, honest failures, the agent workflows and the human-approval flow.

## Layout
`app.py` UI · `sentinel/` (`analytics`, `agent`, `mcp_client`, `db`, `seed`, `launcher`, `api`, `servers/*`) · `tests/` · `scripts/` · `Dockerfile` · `docker-compose.yml`

## Production notes
Auth: map your IdP users to roles (Settings page shows the model). Put MCP servers on an internal network and add bearer-token auth in front when exposing beyond localhost. Optional REST API: `uvicorn sentinel.api:app`.
