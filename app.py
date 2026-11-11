"""SupplyChain Sentinel AI – Streamlit control tower."""
from __future__ import annotations
import json, uuid
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sentinel import analytics as A, db
from sentinel.config import LABELS, ROLES, env
from sentinel.launcher import ensure_servers
from sentinel.mcp_client import get_hub

st.set_page_config(page_title="SupplyChain Sentinel AI", page_icon="🛰️", layout="wide", initial_sidebar_state="expanded")

CSS = """<style>
.block-container{padding-top:1.4rem;max-width:1500px}
[data-testid=stSidebar]{background:linear-gradient(180deg,#0d1526,#0a0f1d);border-right:1px solid #1d2a47}
.brand{font-size:1.25rem;font-weight:700;background:linear-gradient(90deg,#38bdf8,#a78bfa);-webkit-background-clip:text;color:transparent}
.sub{color:#8da2c0;font-size:.82rem}
.card{background:linear-gradient(145deg,#111a2e,#0e1730);border:1px solid #1f2d4d;border-radius:14px;padding:14px 16px;height:100%}
.card .l{color:#8da2c0;font-size:.74rem;text-transform:uppercase;letter-spacing:.06em}
.card .v{font-size:1.65rem;font-weight:700;margin-top:2px}
.card .s{color:#8da2c0;font-size:.78rem}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.72rem;font-weight:600;border:1px solid}
.Critical{color:#fb7185;border-color:#fb718555;background:#fb718518}.High{color:#fb923c;border-color:#fb923c55;background:#fb923c18}
.Medium{color:#facc15;border-color:#facc1555;background:#facc1518}.Low,.live,.connected{color:#34d399;border-color:#34d39955;background:#34d39918}
.demo{color:#38bdf8;border-color:#38bdf855;background:#38bdf818}.disconnected,.error{color:#fb7185;border-color:#fb718555;background:#fb718518}
.None,.Unknown{color:#94a3b8;border-color:#94a3b855}
.alert{border-left:4px solid;border-radius:8px;padding:10px 14px;margin-bottom:8px;background:#111a2e}
.alert.Critical{border-color:#fb7185}.alert.High{border-color:#fb923c}.alert.Medium{border-color:#facc15}.alert.Low{border-color:#34d399}
h1,h2,h3{letter-spacing:-.01em}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

@st.cache_resource(show_spinner="Starting MCP servers…")
def boot():
    procs = ensure_servers()
    return procs

boot()
hub = get_hub()

@st.cache_resource
def get_agent():
    from sentinel.agent import SentinelAgent
    return SentinelAgent()

# ───────────── helpers ─────────────
def pill(t, cls=None): return f'<span class="pill {cls or t}">{t}</span>'
def card(label, value, sub="", color=None):
    c = f"color:{color};" if color else ""
    return f'<div class="card"><div class="l">{label}</div><div class="v" style="{c}">{value}</div><div class="s">{sub}</div></div>'
def head(title, desc):
    st.markdown(f"## {title}"); st.caption(desc)
def usd(x): return f"${x:,.0f}"
def style_fig(fig, h=340):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=h, margin=dict(l=10, r=10, t=40, b=10), font=dict(size=12))
    return fig
RC = {"Critical": "#fb7185", "High": "#fb923c", "Medium": "#facc15", "Low": "#34d399", "None": "#64748b", "Unknown": "#64748b"}
def risk_col(): return st.column_config.TextColumn("Risk")
def show(df, **kw): st.dataframe(df, hide_index=True, width="stretch", **kw)
def role(): return st.session_state.get("role", "approver")

PAGES = [("Overview", "🏠"), ("AI Agent", "🤖"), ("Inventory", "📦"), ("Suppliers", "🏭"), ("Shipments", "🚢"), ("Production", "⚙️"),
         ("Procurement", "🧾"), ("Risk Centre", "🚨"), ("Analytics", "📈"), ("Integrations", "🔌"), ("Settings", "⚙")]
with st.sidebar:
    st.markdown('<div class="brand">🛰️ SupplyChain Sentinel</div><div class="sub">Autonomous supply-chain intelligence · MCP powered</div>', unsafe_allow_html=True)
    st.write("")
    page = st.radio("Navigate", [f"{i}  {n}" for n, i in PAGES], label_visibility="collapsed").split("  ", 1)[1]
    st.divider()
    st.caption("DATA SOURCES")
    rows = db.rows("SELECT service, mode FROM integration_status")
    st.markdown(" ".join(f"{r['service'].replace('_',' ')} {pill(r['mode'])}" for r in rows) or "<span class='sub'>Open Integrations to run checks</span>", unsafe_allow_html=True)
    st.caption(f"Role: **{role()}** · DB: {db.backend_label()}")

# ═════════════════ OVERVIEW ═════════════════
def page_overview():
    head("Executive Control Tower", "A live picture of stock, suppliers, shipments and production – with the issues that need attention first.")
    o = A.overview(); inv = A.inventory_table()
    hs = o["health_score"]; hc = "#34d399" if hs >= 75 else "#facc15" if hs >= 50 else "#fb7185"
    cols = st.columns(6)
    items = [("Inventory value", usd(o["inventory_value"]), "on-hand × standard cost", None), ("Active purchase orders", o["active_pos"], "open / confirmed / in transit", None),
             ("Shipments in transit", o["in_transit"], "tracked now", None), ("Delayed shipments", o["delayed_shipments"], "ETA later than original", "#fb923c" if o["delayed_shipments"] else None),
             ("Critical shortages", o["critical_shortages"], f"+ {o['high_shortages']} high risk", "#fb7185" if o["critical_shortages"] else None),
             ("Operational health", f"{hs:.0f}/100", "heuristic score", hc)]
    for c, (l, v, s, col) in zip(cols, items):
        c.markdown(card(l, v, s, col), unsafe_allow_html=True)
    st.write("")
    a, b = st.columns([3, 2])
    with a:
        d = inv[inv.daily_demand > 0].sort_values("cover_days").head(10)
        fig = px.bar(d, x="cover_days", y="name", orientation="h", color="risk", color_discrete_map=RC, labels={"cover_days": "Days of cover", "name": ""}, title="Days of stock cover – lowest 10 materials")
        fig.add_trace(go.Scatter(x=d.arrival_days, y=d.name, mode="markers", marker=dict(symbol="diamond", size=11, color="#e6edf7"), name="Next inbound (days)"))
        fig.update_yaxes(autorange="reversed"); st.plotly_chart(style_fig(fig, 380), width="stretch")
        st.caption("Bars = how long current stock lasts. Diamonds = when the next delivery is due. A diamond to the right of the bar means a stock-out gap.")
    with b:
        g = go.Figure(go.Indicator(mode="gauge+number", value=hs, title={"text": "Operational health"}, gauge={"axis": {"range": [0, 100]}, "bar": {"color": hc}, "bgcolor": "#111a2e"}))
        st.plotly_chart(style_fig(g, 250), width="stretch"); st.caption(o["health_note"])
        st.markdown(card("Production value at risk", usd(o["production_value_at_risk"]), "open orders estimated to slip", "#fb923c" if o["production_value_at_risk"] else None), unsafe_allow_html=True)
    st.subheader("Priority alerts")
    al = A.alerts("open")[:5]
    if not al: st.success("No open alerts. Run risk detection in the Risk Centre.")
    for x in al:
        st.markdown(f'<div class="alert {x["severity"]}"><b>{x["title"]}</b> {pill(x["severity"])} <span class="sub">· {x["category"]}</span><br><span class="sub">{x["explanation"]}</span><br>➜ {x["recommendation"]}</div>', unsafe_allow_html=True)

# ═════════════════ AI AGENT ═════════════════
SUGGESTIONS = ["Which materials are at risk of running out this week?", "A supplier has delayed my raw material delivery. What will happen?",
               "Which supplier should I choose for polymer resin?", "Notify my procurement team about critical shortages.", "How much should I reorder?", "Show me the KPIs"]

def render_msg(m, key):
    st.markdown(m["content"])
    for t in m.get("tables", []):
        with st.expander(f"📊 {t['title']}", expanded=False):
            if t["rows"]: show(pd.DataFrame(t["rows"]))
    if m.get("recs"):
        st.markdown("**Recommendations**"); [st.markdown(f"- {r}") for r in m["recs"]]
    if m.get("trace"):
        with st.expander(f"🔍 Investigation steps ({len(m['trace'])} MCP calls) · sources: {', '.join(m.get('sources', [])) or '—'}"):
            for i, t in enumerate(m["trace"], 1):
                icon = "✅" if t["ok"] else "❌"
                st.markdown(f"{i}. {icon} `{t['tool']}` on **{t['server']}** MCP server · {t.get('mode')} · {t.get('ms') if t.get('ms') is not None else '–'} ms" + (f" – _{t['error']}_" if t.get("error") else ""))
    p = m.get("pending")
    if p:
        st.warning("**Approval required** – the agent proposes sending this Slack message. Nothing is sent until you approve.")
        st.code(p["text"], language=None); st.caption(f"Channel: {p['channel']} · action #{p['action_id']} · you are acting as **{role()}**")
        c1, c2, _ = st.columns([1, 1, 3])
        for lab, ok_ in (("✅ Approve & send", True), ("🚫 Reject", False)):
            if (c1 if ok_ else c2).button(lab, key=f"{lab}{key}", type="primary" if ok_ else "secondary"):
                try:
                    out = get_agent().resume(m["thread"], ok_, role=role())
                    m.update(content=out["answer"], trace=out["trace"], pending=None)
                    st.rerun()
                except PermissionError as e:
                    st.error(str(e))

def page_agent():
    head("AI Investigation Centre", "Describe a goal in plain language. The agent discovers MCP tools, gathers evidence, and answers – facts, estimates and recommendations are kept separate.")
    st.session_state.setdefault("chat", [])
    st.caption("Mode: **AI Agent** · " + ("LLM summaries on" if (env("GROQ_API_KEY") or env("OLLAMA_MODEL")) else "rule-based planner (add GROQ_API_KEY for LLM summaries)"))
    cols = st.columns(3)
    for i, s in enumerate(SUGGESTIONS):
        if cols[i % 3].button(s, key=f"sg{i}", width="stretch"):
            st.session_state.pending_q = s
    for i, m in enumerate(st.session_state.chat):
        with st.chat_message(m["role"]):
             if m["role"] == "assistant":
                 render_msg(m, i)
             else:
                 st.markdown(m["content"])
    q = st.chat_input("Ask about shortages, delays, suppliers, KPIs…") or st.session_state.pop("pending_q", None)
    if q:
        st.session_state.chat.append({"role": "user", "content": q})

        with st.chat_message("user"):
            st.markdown(q)

    # Handle simple greetings without calling MCP
        greetings = {
            "hi", "hello", "hey", "hey there",
            "hi there", "hello there",
            "good morning", "good afternoon", "good evening"
        }   

        if q.lower().strip(" .,!?") in greetings:
            answer = (
                "Hello! I'm SupplyChain Sentinel AI. "
                "I can help you monitor inventory, suppliers, "
                "shipments, production orders and supply-chain risks. "
                "What would you like to know?"
            )

            st.session_state.chat.append({
                "role": "assistant",
                "content": answer
            })
            st.rerun()

        # Run AI investigation for other questions
        with st.chat_message("assistant"):
            with st.status("Thinking and analysing your question…", expanded=True) as stt:
                tid = str(uuid.uuid4())
                hist = [
                    {"role": m["role"], "content": m["content"][:300]}
                    for m in st.session_state.chat[-6:]
                ]

                out = get_agent().ask(q, tid, hist)

                for t in out.get("trace", []):
                    stt.write(
                        f"{'✅' if t['ok'] else '❌'} "
                        f"`{t['tool']}` ← {t['server']} ({t.get('mode')})"
                    )

                stt.update(
                    label=f"Done – {len(out.get('trace', []))} MCP calls",
                    state="complete",
                    expanded=False
                )

        st.session_state.chat.append({
            "role": "assistant",
            "content": out["answer"],
            "tables": out.get("tables", []),
            "recs": out.get("recs", []),
            "trace": out.get("trace", []),
            "sources": out.get("sources", []),
            "thread": tid,
            "pending": out.get("proposed") if out.get("pending_approval") else None
        })

        st.rerun()
    if st.session_state.chat and st.button("Clear conversation"): st.session_state.chat = []; st.rerun()

# ═════════════════ INVENTORY ═════════════════
def page_inventory():
    head("Inventory Intelligence", "How much stock you have, how long it lasts, and what to reorder. Days of cover = available stock ÷ 30-day average daily demand.")
    inv = A.inventory_table()
    f1, f2, f3 = st.columns([2, 2, 3])
    risk = f1.multiselect("Risk level", ["Critical", "High", "Medium", "Low", "None"]); cat = f2.multiselect("Category", sorted(inv.category.unique())); q = f3.text_input("Search material or SKU")
    d = inv.copy()
    if risk: d = d[d.risk.isin(risk)]
    if cat: d = d[d.category.isin(cat)]
    if q: d = d[d["name"].str.contains(q, case=False) | d.sku.str.contains(q, case=False)]
    t1, t2, t3, t4 = st.tabs(["Stock levels", "Replenishment", "ABC & excess", "Movements"])
    with t1:
        show(d[["sku", "name", "category", "available", "unit", "daily_demand", "cover_days", "lead_time_days", "safety_stock", "reorder_point", "inbound_qty", "arrival_days", "risk", "abc", "turnover"]].rename(columns={
            "sku": "SKU", "name": "Material", "category": "Category", "available": "Available", "unit": "Unit", "daily_demand": "Daily demand", "cover_days": "Days of cover", "lead_time_days": "Lead time (d)",
            "safety_stock": "Safety stock", "reorder_point": "Reorder point", "inbound_qty": "Inbound qty", "arrival_days": "Inbound in (d)", "risk": "Stock-out risk", "abc": "ABC", "turnover": "Turnover (x/yr)"}),
            column_config={"Days of cover": st.column_config.ProgressColumn(min_value=0, max_value=60, format="%.1f"), "Daily demand": st.column_config.NumberColumn(format="%.1f"), "Turnover (x/yr)": st.column_config.NumberColumn(format="%.1f")})
        st.caption("Risk compares days of cover with the next inbound arrival (or supplier lead time if nothing is on order). Turnover is annualised from the last 90 days of issues.")
    with t2:
        plan = A.replenishment_plan()
        if plan:
            p = pd.DataFrame(plan).drop(columns=["material_id", "basis"]); show(p); st.caption(plan[0]["basis"])
            st.download_button("⬇ Download plan (CSV)", p.to_csv(index=False), "replenishment_plan.csv")
        else: st.success("Nothing needs replenishment right now.")
    with t3:
        a, b = st.columns(2)
        abc = inv.groupby("abc").agg(materials=("name", "count"), value=("annual_consumption_value", "sum")).reset_index()
        a.plotly_chart(style_fig(px.pie(abc, names="abc", values="value", hole=.55, title="ABC – share of annual consumption value", color_discrete_sequence=["#38bdf8", "#a78bfa", "#475569"]), 320), width="stretch")
        ex = inv[inv.excess_flag][["name", "cover_days", "excess_qty", "excess_value", "slow_moving"]]
        b.markdown("**Excess & slow-moving stock**"); show(ex.rename(columns={"name": "Material", "cover_days": "Days of cover", "excess_qty": "Excess qty", "excess_value": "Excess value", "slow_moving": "Slow-moving"}))
    with t4:
        m = st.selectbox("Material", inv.name.tolist())
        mid = int(inv[inv["name"] == m].material_id.iloc[0]); s = A.demand_series(mid, 90); fc = A.forecast_demand(mid, 14)
        fig = px.bar(s, x="day", y="qty", title=f"Daily consumption – {m}"); 
        if fc["available"]:
            fx = pd.date_range(pd.Timestamp(A.today()), periods=14); fig.add_trace(go.Scatter(x=fx, y=fc["daily_forecast"], name="Forecast", line=dict(color="#a78bfa", dash="dash")))
        st.plotly_chart(style_fig(fig), width="stretch")
        st.caption((f"Forecast ({fc['method']}): ≈{fc['total']:,.0f} over 14 days – a statistical projection, not a confirmed demand." if fc["available"] else fc["reason"]))

# ═════════════════ SUPPLIERS ═════════════════
def page_suppliers():
    head("Supplier Intelligence", "Who delivers on time, how predictable their lead times are, and how much you depend on them.")
    s = A.supplier_stats()
    cols = st.columns(4)
    cols[0].markdown(card("Suppliers", len(s), "active"), unsafe_allow_html=True)
    cols[1].markdown(card("Avg on-time rate", f"{(s.otd * s.closed_pos).sum() / s.closed_pos.sum():.0%}", "all received POs"), unsafe_allow_html=True)
    cols[2].markdown(card("High/Critical risk", int(s.risk.isin(['High', 'Critical']).sum()), "by reliability score", "#fb923c"), unsafe_allow_html=True)
    cols[3].markdown(card("Open delayed POs", int(s.open_delayed.sum()), "need follow-up", "#fb7185" if s.open_delayed.sum() else None), unsafe_allow_html=True)
    st.write("")
    t = s.sort_values("score", ascending=False)[["name", "country", "category", "closed_pos", "otd", "avg_lead", "lead_std", "fill_rate", "otif", "price_var", "spend_share", "primary_materials", "open_delayed", "score", "risk"]]
    show(t.rename(columns={"name": "Supplier", "country": "Country", "category": "Category", "closed_pos": "POs", "otd": "On-time", "avg_lead": "Avg lead (d)", "lead_std": "Lead σ (d)", "fill_rate": "Fill rate", "otif": "OTIF",
                           "price_var": "Price var.", "spend_share": "Spend share", "primary_materials": "Primary for", "open_delayed": "Open delayed", "score": "Score", "risk": "Risk"}),
         column_config={"On-time": st.column_config.NumberColumn(format="percent"), "Fill rate": st.column_config.NumberColumn(format="percent"), "OTIF": st.column_config.NumberColumn(format="percent"),
                        "Price var.": st.column_config.NumberColumn(format="percent"), "Spend share": st.column_config.NumberColumn(format="percent"), "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                        "Avg lead (d)": st.column_config.NumberColumn(format="%.1f"), "Lead σ (d)": st.column_config.NumberColumn(format="%.1f")})
    st.caption("Score (0-100) = 45% on-time + 25% fill rate + 15% lead-time stability + 15% price discipline – a heuristic from purchase-order history.")
    fig = px.scatter(s.dropna(subset=["otd"]), x="lead_std", y="otd", size="spend", color="risk", hover_name="name", color_discrete_map=RC, title="Reliability map – bubble size = spend",
                     labels={"lead_std": "Lead-time variability (days, σ)", "otd": "On-time delivery rate"})
    fig.update_yaxes(tickformat=".0%"); st.plotly_chart(style_fig(fig, 380), width="stretch")
    st.subheader("Compare suppliers for a material")
    mats = db.df("SELECT name FROM materials ORDER BY name").name.tolist()
    c1, c2 = st.columns([3, 1]); m = c1.selectbox("Material", mats, key="cmp_m"); qty = c2.number_input("Quantity (0 = MOQ)", 0.0, step=100.0)
    c = A.compare_suppliers(m, qty)
    show(pd.DataFrame(c["options"])[["supplier", "country", "unit_price", "lead_time_days", "otd", "score", "risk", "qty", "total_cost", "composite"]])
    st.success(c["reasoning"]); st.caption(c["caveat"])

# ═════════════════ SHIPMENTS ═════════════════
def page_shipments():
    head("Shipment Intelligence", "Where shipments are, how late they are, and whether weather along the way could make it worse.")
    s = A.shipments_table(); act = s[s.active]
    c = st.columns(4)
    c[0].markdown(card("Active shipments", len(act)), unsafe_allow_html=True)
    c[1].markdown(card("Delayed", int(act.delayed.sum()), "vs original ETA", "#fb923c"), unsafe_allow_html=True)
    c[2].markdown(card("Delay rate (all history)", f"{s.delayed.mean():.0%}", "actual"), unsafe_allow_html=True)
    c[3].markdown(card("Longest delay", f"{int(act.delay_days.max())} d" if len(act) else "–"), unsafe_allow_html=True)
    view = st.radio("View", ["Active", "Delayed only", "All history"], horizontal=True)
    d = act[act.delayed] if view == "Delayed only" else act if view == "Active" else s
    show(d[["po_number", "supplier", "materials", "carrier", "status", "eta_original", "eta_current", "delay_days", "current_location", "last_event"]].rename(columns=lambda x: x.replace("_", " ").title()))
    if len(act):
        st.plotly_chart(style_fig(px.bar(act, x="po_number", y="delay_days", color="supplier", title="Delay vs original ETA (days)"), 300), width="stretch")
        loc = act.dropna(subset=["lat"]).drop_duplicates("current_location")
        if len(loc):
            st.map(loc.rename(columns={"lat": "latitude", "lon": "longitude"})[["latitude", "longitude"]], size=40000)
        st.subheader("Weather risk on a shipment's current position")
        pick = st.selectbox("Shipment", act.po_number + " – " + act.current_location.fillna(""))
        if st.button("Check weather via MCP"):
            r = act[act.po_number == pick.split(" – ")[0]].iloc[0]
            w = hub.call("get_weather_conditions", {"location": r.current_location, "latitude": r.lat, "longitude": r.lon, "days": 5})
            if w["ok"]:
                risk = w["data"]["risk"]; st.markdown(f"Route risk: {pill(risk['level'])} · source: **{w['source']}** ({w['mode']})", unsafe_allow_html=True)
                for x in risk["reasons"]: st.write("• " + x)
                show(pd.DataFrame(w["data"]["forecast"]))
            else: st.error(w["error"])

# ═════════════════ PRODUCTION ═════════════════
def page_production():
    head("Production Impact Analysis", "Open production orders are checked against stock and inbound deliveries to estimate delays – assuming current ETAs hold.")
    pi = A.production_impact(); S_ = pi["summary"]
    c = st.columns(3)
    c[0].markdown(card("Open orders", S_["orders"]), unsafe_allow_html=True); c[1].markdown(card("Orders at risk", S_["at_risk"], color="#fb923c"), unsafe_allow_html=True)
    c[2].markdown(card("Value at risk", usd(S_["value_at_risk"]), f"max delay {S_['max_delay_days']} d", "#fb7185" if S_["value_at_risk"] else None), unsafe_allow_html=True)
    st.caption(pi["assumption"])
    for o in pi["orders"]:
        with st.expander(f"{o['order_number']} · {o['product']} · due {o['due_date']} · est. delay {o['est_delay_days']} d · {o['risk']}", expanded=o["risk"] in ("Critical", "High")):
            show(pd.DataFrame(o["lines"]).drop(columns=["delay_days"]))
            if o["value_at_risk"]: st.warning(f"{usd(o['value_at_risk'])} of order value at risk. Options: expedite constraining material, re-sequence orders, or ship partial batch.")
    st.caption("Schedule adherence history is on the Analytics page.")

# ═════════════════ PROCUREMENT ═════════════════
def page_procurement():
    head("Procurement Intelligence", "Monitor open purchase orders, compare quotations and decide how much to buy.")
    r = hub.call("get_purchase_orders", {"status": "open"})
    st.subheader("Open purchase orders (via MCP data server)")
    if r["ok"]: show(pd.DataFrame(r["data"]).drop(columns=["po_id", "received_date"], errors="ignore")); st.caption(f"Source: {r['source']} ({r['mode']})")
    else: st.error(r["error"])
    st.subheader("Procurement cost variance by supplier")
    s = A.supplier_stats().dropna(subset=["price_var"]); st.plotly_chart(style_fig(px.bar(s, x="name", y="price_var", title="(Paid − standard) ÷ standard", labels={"price_var": "Variance", "name": ""}).update_yaxes(tickformat=".1%"), 300), width="stretch")
    st.subheader("Reorder recommendations"); plan = A.replenishment_plan()
    if plan: show(pd.DataFrame(plan).drop(columns=["material_id", "basis"])); st.metric("Estimated cost of suggested orders", usd(sum(p["est_cost"] for p in plan)))
    st.caption("Suggested quantities are estimates; purchase orders are never created automatically.")

# ═════════════════ RISK CENTRE ═════════════════
def page_risk():
    head("Risk & Alert Centre", "Rule-based detection across stock, shipments, suppliers and production – every alert shows its evidence and a recommended fix.")
    c1, c2 = st.columns([1, 4])
    if c1.button("▶ Run risk detection", type="primary"):
        res = A.detect_risks(); db.audit("user", "run_risk_detection", "alerts", {}, "executed", res); c2.success(f"{res['detected']} risks detected, {res['cleared']} cleared.")
    status = st.radio("Status", ["open", "cleared", "resolved", "all"], horizontal=True)
    sev = st.multiselect("Severity", ["Critical", "High", "Medium", "Low"], default=["Critical", "High", "Medium", "Low"])
    for a in [x for x in A.alerts("" if status == "all" else status) if x["severity"] in sev]:
        with st.expander(f"[{a['severity']}] {a['title']} · {a['category']}"):
            st.write(a["explanation"]); aff = json.loads(a["affected"] or "[]")
            if aff: st.write("**Affected:** " + "; ".join(aff))
            st.write("**Recommended action:** " + a["recommendation"]); st.caption(f"Evidence: {a['evidence']} · detected {a['created_at']} · updated {a['updated_at']}")
            if a["status"] == "open" and st.button("Mark resolved", key=f"res{a['id']}"):
                db.execute("UPDATE risk_alerts SET status='resolved', updated_at=:n WHERE id=:i", n=db.now(), i=a["id"]); db.audit("user", "resolve_alert", str(a["id"]), {}, "executed"); st.rerun()

# ═════════════════ ANALYTICS ═════════════════
def page_analytics():
    head("KPI & Analytics", "Every KPI is calculated from stored records. Tags show whether a value is an actual historical metric or an estimate; unavailable KPIs explain why.")
    ks = A.kpis(); tag = {"actual": "#34d399", "estimate": "#facc15", "forecast": "#a78bfa"}
    for i in range(0, len(ks), 4):
        cols = st.columns(4)
        for c, k in zip(cols, ks[i:i + 4]):
            v = "n/a" if k["value"] is None else (f"{k['value']:,.0f}" if abs(k["value"]) >= 1000 else f"{k['value']:,.1f}")
            c.markdown(card(k["label"], f"{v} <span style='font-size:.8rem'>{k['unit']}</span>", f"<span style='color:{tag[k['basis']]}'>● {k['basis']}</span> · {k['unavailable_reason'] or k['note']}"), unsafe_allow_html=True)
        st.write("")
    inv = A.inventory_table()
    a, b = st.columns(2)
    a.plotly_chart(style_fig(px.bar(inv.sort_values("inventory_value", ascending=False), x="name", y="inventory_value", color="abc", title="Inventory value by material (ABC colour)", labels={"name": "", "inventory_value": "USD"}), 340), width="stretch")
    done = db.df("SELECT order_number, due_date, completed_date FROM production_orders WHERE status='completed'")
    if len(done):
        done["days_late"] = (pd.to_datetime(done.completed_date) - pd.to_datetime(done.due_date)).dt.days
        b.plotly_chart(style_fig(px.bar(done, x="order_number", y="days_late", title="Production schedule adherence – days late (negative = early)"), 340), width="stretch")

# ═════════════════ INTEGRATIONS ═════════════════
def page_integrations():
    head("Integrations", "Every integration is its own MCP server. Status below is measured live – never assumed.")
    if st.button("🔄 Run connection checks", type="primary") or "health" not in st.session_state:
        st.session_state.health = hub.health()
    for h in st.session_state.health:
        with st.container(border=True):
            c = st.columns([3, 1, 1, 4])
            c[0].markdown(f"**{h['service']}**<br><span class='sub'>{h['url']}</span>", unsafe_allow_html=True)
            c[1].markdown(pill(h["server"].split(":")[0], "live" if h["server"] == "up" else "disconnected") + " <span class='sub'>MCP</span>", unsafe_allow_html=True)
            c[2].markdown(pill(h["mode"]), unsafe_allow_html=True); c[3].caption(h["detail"])
            with st.expander(f"Tools ({len(h['tools'])})"): st.code("\n".join(h["tools"]) or "–")
    st.info("**connected/live** = real external service responded · **demo** = realistic sample data, clearly flagged, writes disabled · **disconnected** = credentials missing or service down; no fake results are ever returned.")
    with st.expander("Setup guide"):
        st.markdown("""
- **Supabase**: copy the *Connection string (URI)* into `DATABASE_URL`; tables are created automatically on first start (or run `sentinel/schema.sql`).
- **Google Sheets**: create a service account, download JSON → `GOOGLE_SERVICE_ACCOUNT_FILE`; share your spreadsheet with its e-mail; set `GOOGLE_SHEET_ID`. Run `python scripts/seed_google_sheet.py` to push the demo template (worksheets *Inventory, Suppliers, PurchaseOrders*).
- **Slack**: create an app with `chat:write`, install it, set `SLACK_BOT_TOKEN`, invite the bot to your channel, set `SLACK_DEFAULT_CHANNEL`.
- **Logistics API**: set `LOGISTICS_API_URL` (+ `LOGISTICS_API_KEY`) – see README for the expected JSON.
- **Weather**: Open-Meteo works without a key. `WEATHER_MODE=demo` for offline.
Restart the app after editing `.env`.""")

# ═════════════════ SETTINGS ═════════════════
def page_settings():
    head("Settings & Audit", "Access role, runtime information and the full audit trail of approvals and actions.")
    st.selectbox("Your role", ROLES, index=ROLES.index(role()), key="role", help="viewer < analyst < approver < admin. Only approver/admin can approve external actions.")
    st.caption("Role-based design: approvals and write tools verify the approver role server-side. Plug in your identity provider to map real users to roles.")
    c = st.columns(3); c[0].metric("Database", db.backend_label()); c[1].metric("LLM", "Groq" if env("GROQ_API_KEY") else "Ollama" if env("OLLAMA_MODEL") else "Rule-based"); c[2].metric("Weather", env("WEATHER_MODE", "live"))
    st.subheader("Audit log")
    d = db.df("SELECT id, created_at, actor, action, target, status, approved_by, approved_role FROM action_history ORDER BY id DESC LIMIT 200")
    show(d) if len(d) else st.caption("No actions recorded yet.")
    st.subheader("Past AI investigations")
    show(db.df("SELECT created_at, query, intent, tools_used FROM ai_investigations ORDER BY id DESC LIMIT 25"))
    if db.is_sqlite():
        with st.expander("Danger zone"):
            if st.button("Reset demo database"): db.reset_demo(); st.success("Demo data restored."); st.rerun()

{"Overview": page_overview, "AI Agent": page_agent, "Inventory": page_inventory, "Suppliers": page_suppliers, "Shipments": page_shipments, "Production": page_production,
 "Procurement": page_procurement, "Risk Centre": page_risk, "Analytics": page_analytics, "Integrations": page_integrations, "Settings": page_settings}[page]()
