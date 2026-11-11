"""Sentinel Intelligence – LangGraph agent that investigates through MCP tools.

Graph:  plan → gather → compose → (notify intent?) propose → [HUMAN APPROVAL interrupt] → send → finalize
• Tool selection: rule-based intent routing, optionally extended by an LLM planner (read-only tools only).
• All data comes from MCP tool calls; the LLM (if configured) only writes the summary from retrieved evidence.
• Consequential actions (Slack) pause the graph; nothing runs until a human approves.
"""
from __future__ import annotations
import json, math, re
from typing import Any, Optional, TypedDict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from . import db
from .config import env
from .mcp_client import get_hub

WRITE_TOOLS = {"send_slack_notification", "update_inventory_record", "sheets_update_inventory_record", "run_risk_detection"}

class S(TypedDict, total=False):
    query: str; history: list; intent: str; plan: list; evidence: dict; trace: list; errors: list
    answer: str; tables: list; recs: list; sources: list; proposed: dict; approved: Optional[bool]; result: dict; llm_used: bool

PLANS = {
    "greeting": [],
    "general": [],
    "shortage": [("calculate_stockout_risk", {"horizon_days": 7}), ("calculate_production_impact", {}), ("generate_replenishment_plan", {})],
    "delay": [("get_shipment_status", {"delayed_only": True}), ("get_purchase_orders", {"status": "open"}), ("get_supplier_performance", {}),
              ("calculate_stockout_risk", {"horizon_days": 14}), ("calculate_production_impact", {})],
    "supplier_choice": [("get_inventory_levels", {}), ("generate_replenishment_plan", {})],
    "notify": [("calculate_stockout_risk", {"horizon_days": 7}), ("calculate_production_impact", {})],
    "replenish": [("generate_replenishment_plan", {}), ("calculate_stockout_risk", {"horizon_days": 14})],
    "weather": [("get_shipment_status", {})],
    "kpi": [("get_kpis", {}), ("get_supplier_performance", {})],
    "production": [("calculate_production_impact", {}), ("calculate_stockout_risk", {"horizon_days": 14})],
    "overview": [("get_kpis", {}), ("get_risk_alerts", {"status": "open"}), ("calculate_stockout_risk", {"horizon_days": 7})],
}

def rule_intent(q: str) -> str:
    q = q.lower().strip(" .,!?")

    # Normal explanations and project-help questions
    if re.search(
        r"\b(what is|what are|define|meaning of|explain|"
        r"what does .+ mean|how do i use|how can i use|"
        r"how does .+ work|help me understand)\b",
        q
    ):
        return "general"

    if re.search(r"\b(notify|slack|inform|send)\b", q) and re.search(
        r"\b(team|procurement|channel|buyers?|shortages?)\b", q
    ):
        return "notify"

    if re.search(
        r"which supplier|best supplier|choose|compare supplier|"
        r"supplier should|who should (i|we) buy|alternative supplier", q
    ):
        return "supplier_choice"

    if re.search(r"\b(delay|delayed|late|behind|held up|customs)\b", q):
        return "delay"

    if re.search(r"weather|storm|typhoon|rain|route|cyclone", q):
        return "weather"

    if re.search(
        r"reorder|replenish|how much (to )?(order|buy)|"
        r"order quantity|purchase plan", q
    ):
        return "replenish"

    if re.search(
        r"run out|running out|shortage|stock-?out|"
        r"low stock|at risk|this week|material", q
    ):
        return "shortage"

    if re.search(r"kpi|performance|otif|on-?time|turnover|metrics|scorecard", q):
        return "kpi"

    if re.search(r"production|work order|manufactur", q):
        return "production"

    if re.search(r"\b(overview|dashboard|overall status|supply chain status)\b", q):
        return "overview"

    return "general"

def n(x, d=0):
    return "n/a" if x is None else f"{x:,.{d}f}"

def stock_line(r: dict) -> str:
    cov = "no recent demand" if r.get("cover_days") is None else f"≈{r['cover_days']:.1f} days of cover"
    arr = (f"next inbound {n(r['inbound_qty'])} {r['unit']} due in {int(r['arrival_days'])} days" if r.get("arrival_days") is not None else "no inbound order")
    return f"**{r['name']}** – {n(r['available'])} {r['unit']} on hand, {n(r['daily_demand'], 1)}/day (30-day avg) → {cov}; {arr} → **{r['risk']}**"

def ev(state: S, tool: str):
    r = (state.get("evidence") or {}).get(tool)
    return r.get("data") if r and r.get("ok") else None

def get_llm():
    try:
        if env("GROQ_API_KEY"):
            from langchain_groq import ChatGroq
            return ChatGroq(model=env("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0, timeout=30, max_retries=1)
        if env("OLLAMA_MODEL"):
            from langchain_ollama import ChatOllama
            return ChatOllama(model=env("OLLAMA_MODEL"), temperature=0)
    except Exception:
        return None
    return None

def build_agent():
    hub = get_hub()
    llm = get_llm()

    # ── node: plan ─────────────────────────────────────────────
    def plan(state: S) -> dict:
        intent = rule_intent(state["query"])

        # General questions do not need MCP calls
        if intent == "general":
            return {
                "intent": intent,
                "plan": [],
                "evidence": {},
                "trace": [],
                "errors": [],
                "tables": [],
                "recs": [],
                "sources": [],
                "llm_used": False
            }

        tools = hub.discover()
        
        
        calls = [(t, dict(a)) for t, a in PLANS[intent] if t in tools]
        
        
        if intent in ("greeting", "general"):
            return {
                "intent": intent,
                "plan": [],
                "evidence": {},
                "trace": [],
                "errors": [],
                "tables": [],
                "recs": [],
                "sources": [],
                "llm_used": False
            }
        
        
        
        if llm:
            try:
                catalog = "\n".join(f"- {k}({', '.join((v['schema'].get('properties') or {}).keys())}): {v['description'][:90]}" for k, v in tools.items()
                                    if k not in WRITE_TOOLS and not k.endswith("_status"))
                out = llm.invoke([("system", "You select read-only tools for a supply-chain analyst. Reply ONLY with JSON: {\"extra_calls\":[{\"tool\":str,\"args\":{}}]} (max 2 extra calls, may be empty). Tools:\n" + catalog),
                                  ("human", f"History: {json.dumps(state.get('history', [])[-4:])}\nQuestion: {state['query']}\nBase plan: {[c[0] for c in calls]}")]).content
                extra = json.loads(re.search(r"\{.*\}", out, re.S).group(0)).get("extra_calls", [])[:2]
                for e in extra:
                    t, a = e.get("tool"), e.get("args") or {}
                    if t in tools and t not in WRITE_TOOLS and t not in [c[0] for c in calls] and set(a) <= set((tools[t]["schema"].get("properties") or {})):
                        calls.append((t, a))
            except Exception:
                pass
        return {"intent": intent, "plan": [{"tool": t, "args": a} for t, a in calls], "evidence": {}, "trace": [], "errors": [], "tables": [], "recs": [], "sources": [], "llm_used": False}

    # ── node: gather (MCP tool execution + follow-up enrichment) ──
    def gather(state: S) -> dict:
        evidence, trace, errors = dict(state.get("evidence", {})), list(state.get("trace", [])), list(state.get("errors", []))
        def run(tool, args):
            r = hub.call(tool, args)
            m = r.pop("_meta", {})
            trace.append({"tool": tool, "server": m.get("server"), "ms": m.get("ms"), "ok": r.get("ok"), "mode": r.get("mode"), "args": args, "error": r.get("error")})
            if not r.get("ok"):
                errors.append(f"{tool}: {r.get('error')}")
            return r
        for step in state["plan"]:
            evidence[step["tool"]] = run(step["tool"], step["args"])
        intent = state["intent"]
        if intent in ("delay", "weather"):
            ships = (evidence.get("get_shipment_status") or {}).get("data") or []
            seen = []
            for s in sorted([x for x in ships if x.get("lat") is not None], key=lambda x: -(x.get("delay_days") or 0)):
                if s["current_location"] in seen or len(seen) >= 3:
                    continue
                seen.append(s["current_location"])
                evidence[f"weather:{s['current_location']}"] = run("get_weather_conditions", {"location": s["current_location"], "latitude": s["lat"], "longitude": s["lon"], "days": 5})
        if intent == "supplier_choice":
            inv = (evidence.get("get_inventory_levels") or {}).get("data") or []
            q = state["query"].lower()
            hit = [r for r in inv if r["name"].lower() in q or r["sku"].lower() in q or any(w in q for w in r["name"].lower().split() if len(w) > 4)]
            targets = [hit[0]["name"]] if hit else [p["material"] for p in ((evidence.get("generate_replenishment_plan") or {}).get("data") or [])[:3]]
            for t in targets:
                evidence[f"compare:{t}"] = run("compare_supplier_options", {"material": t})
        return {"evidence": evidence, "trace": trace, "errors": errors}

    # ── node: compose (deterministic, evidence-backed) ─────────
    def compose(state: S) -> dict:
        intent, E = state["intent"], state["evidence"]

        # Give general questions to the LLM
        if intent == "general":
            if llm:
                try:
                    project_context = """
                    You are SupplyChain Sentinel AI, an intelligent assistant.

                    ABOUT THIS PROJECT:
                    This is a Streamlit-based supply-chain control tower.
                    Its AI Agent uses LangGraph and MCP integrations.

            MAIN FEATURES:
            1. Overview: inventory value, purchase orders, shipments,
               shortages, operational health and priority alerts.
            2. Inventory: stock levels, days of cover, reorder points,
               replenishment, ABC analysis and stock movements.
            3. Suppliers: supplier reliability, on-time delivery,
               lead times, price variation and supplier comparison.
            4. Shipments: tracking, delivery delays, locations and weather risk.
            5. Production: production orders, material shortages,
               estimated delays and value at risk.
            6. Procurement: open purchase orders, cost variance
               and reorder recommendations.
            7. Risk Centre: risk detection, alerts and recommended actions.
            8. Analytics: KPIs, inventory value and production performance.
            9. Integrations: MCP servers, Google Sheets, Slack,
               logistics API and weather.
            10. Settings: user roles, database, LLM status and audit history.

            IMPORTANT PROJECT RULES:
            - Never invent live inventory, sales, supplier or shipment figures.
            - Explain when information is demo data, live data or an estimate.
            - Slack notifications require human approval.
            - Purchase orders are not automatically created.
            - Explain features in simple, practical steps.
            - If asked about a feature, explain what it does and how to use it.
            - If asked about an error, explain the likely cause and request
              the relevant code or error message when needed.
            """

                    response = llm.invoke([
                        (
                            "system",
                            project_context + """
                            Answer like a capable, professional general-purpose AI.
                            You can answer general knowledge, mathematics,
                            greetings, explanations and project-related questions.

                            Do not force every question to be about supply chains.
                            Do not repeat a standard fallback message.
                            Understand the actual question and answer it directly.
                            Use conversation history when relevant.
                            Be clear, natural, helpful and appropriately detailed.
                            Never claim to have inspected files or live data
                            that were not provided.
                            """
                        ),
                        (
                            "human",
                            f"Conversation history: {json.dumps(state.get('history', [])[-6:])}\n"
                            f"User question: {state['query']}"
                        )
                    ])

                    answer = response.content
                    if not isinstance(answer, str):
                        answer = str(answer)

                    return {
                        "answer": answer,
                        "tables": [],
                        "recs": [],
                        "sources": [],
                        "llm_used": True
                    }

                except Exception as e:
                    return {
                        "answer": (
                            "I couldn't generate a response right now. "
                            "Please check your LLM connection and API configuration. "
                            f"Technical detail: {e}"
                        ),
                        "tables": [],
                        "recs": [],
                        "sources": [],
                        "llm_used": False
                    }

            return {
                "answer": (
                    "General AI responses are not enabled yet. "
                    "Please configure GROQ_API_KEY or OLLAMA_MODEL."
                ),
                "tables": [],
                "recs": [],
                "sources": [],
                "llm_used": False
            }

        
        tables, recs, L = [], [], []
        stock = ev(state, "calculate_stockout_risk") or []
        impact = (ev(state, "calculate_production_impact") or {}).get("orders", [])
        plan_ = {p["material"]: p for p in (ev(state, "generate_replenishment_plan") or [])}
        urgent = [r for r in stock if r["risk"] in ("Critical", "High")]
        late_orders = [o for o in impact if o["value_at_risk"] > 0]
        def stock_section(rows, title="Stock position"):
            L.append(f"**{title}** *(facts: on-hand & inbound ETAs · estimates: days of cover from 30-day average demand)*")
            L.extend(f"- {stock_line(r)}" for r in rows[:8])
            tables.append({"title": title, "rows": [{"Material": r["name"], "Available": r["available"], "Unit": r["unit"], "Daily demand": round(r["daily_demand"], 1),
                          "Cover (days)": None if r["cover_days"] is None else round(r["cover_days"], 1), "Next inbound (days)": r["arrival_days"], "Risk": r["risk"]} for r in rows]})
        def impact_section(orders):
            if not orders:
                L.append("**Production** – no open order is currently estimated to slip."); return
            L.append("**Production impact** *(estimate – assumes current ETAs hold)*")
            for o in orders:
                bits = "; ".join(f"{l['material']}: {l['status']}" for l in o["lines"] if l["shortfall"] > 0)
                L.append(f"- **{o['order_number']}** {o['product']} (due {o['due_date']}, {n(o['value_at_risk'])} USD at risk): est. delay **{o['est_delay_days']} d**{' + uncovered material' if o['material_uncovered'] else ''} → {bits}")
            tables.append({"title": "Production orders at risk", "rows": [{"Order": o["order_number"], "Product": o["product"], "Due": o["due_date"], "Est. delay (d)": o["est_delay_days"],
                           "Uncovered": o["material_uncovered"], "Value at risk (USD)": o["value_at_risk"], "Risk": o["risk"]} for o in orders]})
        def rec_for(r):
            p = plan_.get(r["name"])
            if r.get("inbound_qty", 0) > 0 and r.get("arrival_days") is not None and r["cover_days"] is not None and r["arrival_days"] > r["cover_days"]:
                s = f"Expedite inbound {r['name']} ({r.get('inbound_pos')}) – stock lasts ~{r['cover_days']:.1f} d but arrival is in {int(r['arrival_days'])} d."
            else:
                s = f"Raise a purchase order for {r['name']}."
            return s + (f" Suggested top-up: {p['suggested_qty']:,.0f} {r['unit']} from {p['supplier']} (≈{p['est_cost']:,.0f} USD, estimate)." if p else "")

        if intent in ("shortage", "notify", "replenish", "production"):
            if urgent:
                L.append(f"{len(urgent)} material(s) face a stock-out before replenishment arrives ({sum(r['risk']=='Critical' for r in urgent)} Critical, {sum(r['risk']=='High' for r in urgent)} High).")
            else:
                L.append("No material is currently at Critical/High stock-out risk.")
            if stock: stock_section(stock)
            if intent != "replenish": impact_section(late_orders)
            recs += [rec_for(r) for r in urgent]
            if intent == "replenish" and plan_:
                tables.append({"title": "Replenishment plan", "rows": [{k: v for k, v in p.items() if k not in ("basis", "material_id", "sku")} for p in plan_.values()]})
                L.append("**Replenishment plan** *(estimate: target = reorder point + 30 days demand)*")
                L.extend(f"- {p['material']}: order **{p['suggested_qty']:,.0f} {p['unit']}** from {p['supplier']} (≈{p['est_cost']:,.0f} USD) – {p['action']}" for p in plan_.values())
                recs += [f"{p['action']}: {p['material']} – {p['suggested_qty']:,.0f} {p['unit']} from {p['supplier']}." for p in plan_.values() if p["risk"] in ("Critical", "High")]
        elif intent == "delay":
            ships = ev(state, "get_shipment_status") or []
            if ships:
                L.append(f"**Delayed shipments (facts from tracking source)** – {len(ships)} found:")
                L.extend(f"- {s['po_number']} · {s['supplier']} · {s['materials']}: ETA moved {s['eta_original']} → {s['eta_current']} (**+{s['delay_days']} d**). Last event: {s['last_event']} at {s['current_location']}." for s in ships)
                tables.append({"title": "Delayed shipments", "rows": [{"PO": s["po_number"], "Supplier": s["supplier"], "Materials": s["materials"], "Original ETA": s["eta_original"], "Current ETA": s["eta_current"], "Delay (d)": s["delay_days"], "Location": s["current_location"]} for s in ships]})
            else:
                L.append("No delayed shipments are reported by the tracking source.")
            for k, v in E.items():
                if k.startswith("weather:") and v.get("ok"):
                    w = v["data"]; L.append(f"- Weather near **{k[8:]}** ({v['mode']}): route risk **{w['risk']['level']}**" + (f" – {w['risk']['reasons'][0]}" if w['risk']['reasons'] else "") )
                    if v["mode"] == "demo": L[-1] += " *(synthetic demo weather)*"
            affected = [r for r in stock if any(r["name"] in (s.get("materials") or "") for s in ships)]
            if affected: stock_section(affected, "Materials affected by delays")
            impact_section(late_orders)
            for r in affected: recs.append(rec_for(r))
            for s in ships: recs.append(f"Contact {s['supplier']} for a confirmed ETA on {s['po_number']}; evaluate air freight or an alternate supplier if the delay exceeds cover.")
        elif intent == "supplier_choice":
            cmps = [(k[8:], v["data"]) for k, v in E.items() if k.startswith("compare:") and v.get("ok")]
            for mat, c in cmps:
                L.append(f"**{mat}** – recommended: **{c['recommended']}**. {c['reasoning']}.")
                tables.append({"title": f"Supplier options – {mat}", "rows": [{"Supplier": o["supplier"], "Unit price": o["unit_price"], "Lead time (d)": o["lead_time_days"], "On-time %": None if o["otd"] is None else round(o["otd"] * 100), "Reliability score": None if o["score"] is None else round(o["score"]), "Risk": o["risk"], f"Total cost for {c['quantity_basis']:,.0f}": round(o["total_cost"], 2), "Composite": round(o["composite"], 1)} for o in c["options"]]})
                recs.append(f"For {mat}: choose {c['recommended']} (weights: price 40%, reliability 35%, lead time 25%). Cheapest option is not always most reliable – review the table.")
            if not cmps: L.append("I could not retrieve supplier quotations for that material.")
        elif intent == "weather":
            ships = ev(state, "get_shipment_status") or []
            L.append(f"Tracking {len(ships)} active shipments.")
            for k, v in E.items():
                if k.startswith("weather:") and v.get("ok"):
                    w = v["data"]["risk"]; L.append(f"- **{k[8:]}**: {w['level']} risk" + (f" ({'; '.join(w['reasons'][:2])})" if w["reasons"] else "") + (" *(demo data)*" if v["mode"] == "demo" else ""))
        elif intent in ("kpi", "overview"):
            ks = ev(state, "get_kpis") or []
            L.append("**KPIs** *(actual = history · estimate = current data + assumptions)*")
            L.extend(f"- {k['label']}: **{n(k['value'], 1)} {k['unit']}** ({k['basis']})" if k["value"] is not None else f"- {k['label']}: n/a – {k['unavailable_reason']}" for k in ks[:14])
            tables.append({"title": "KPIs", "rows": [{"KPI": k["label"], "Value": k["value"], "Unit": k["unit"], "Basis": k["basis"], "Note": k["note"] or k["unavailable_reason"]} for k in ks]})
            if intent == "overview":
                al = ev(state, "get_risk_alerts") or []
                L.append(f"**Open alerts: {len(al)}**"); L.extend(f"- [{a['severity']}] {a['title']} – {a['explanation']}" for a in al[:5])
                recs += [a["recommendation"] for a in al[:3]]
        for k, v in E.items():
            if k not in {t for t, _ in PLANS[intent]} and not k.startswith(("weather:", "compare:")) and v.get("ok"):
                tables.append({"title": f"Additional evidence – {k}", "rows": v["data"] if isinstance(v["data"], list) else [v["data"]]})
        if state.get("errors"):
            L.append("**Data gaps** – these tool calls failed, so related conclusions may be incomplete:\n" + "\n".join(f"- {e}" for e in state["errors"]))
        sources = sorted({f"{t['server']} ({t['mode']})" for t in state["trace"] if t.get("ok")})
        text = "\n".join(L)
        if llm and text:
            try:
                s = llm.invoke([("system", "You are Sentinel, a supply-chain analyst. Write a 3-4 sentence executive summary using ONLY the evidence given. Do not invent numbers. Distinguish facts from estimates."),
                                ("human", f"Question: {state['query']}\nEvidence:\n{text[:5000]}\nRecommendations: {recs[:5]}")]).content
                text = f"**Summary:** {s.strip()}\n\n" + text
                state_llm = True
            except Exception:
                state_llm = False
        else:
            state_llm = False
        return {"answer": text, "tables": tables, "recs": list(dict.fromkeys(recs)), "sources": sources, "llm_used": state_llm}

    # ── node: propose (Slack message → pending approval) ───────
    def propose(state: S) -> dict:
        stock = [r for r in (ev(state, "calculate_stockout_risk") or []) if r["risk"] in ("Critical", "High")]
        orders = [o for o in (ev(state, "calculate_production_impact") or {}).get("orders", []) if o["value_at_risk"] > 0]
        lines = [f"🚨 *Sentinel – critical shortage alert* ({db.now()[:10]})"]
        lines += [f"• {r['name']}: {r['available']:,.0f} {r['unit']} left (~{r['cover_days']:.1f} d), " +
                  (f"next inbound in {int(r['arrival_days'])} d" if r.get("arrival_days") is not None else "no inbound order") + f" – {r['risk']}" for r in stock[:8]]
        if orders:
            lines.append("Production at risk: " + "; ".join(f"{o['order_number']} (+{o['est_delay_days']} d)" for o in orders[:4]))
        lines.append("_Estimates use 30-day average demand and current ETAs. Please confirm actions with procurement._")
        ch = env("SLACK_DEFAULT_CHANNEL", "#procurement")
        text = "\n".join(lines)
        aid = db.create_action("sentinel-agent", "send_slack", ch, {"channel": ch, "text": text})
        return {"proposed": {"action_id": aid, "channel": ch, "text": text}}

    # ── node: send (runs only after human approval) ────────────
    def send(state: S) -> dict:
        p = state["proposed"]
        if state.get("approved") is not True:
            db.set_action(p["action_id"], status="rejected")
            return {"result": {"ok": False, "status": "rejected", "error": "Rejected by user – nothing was sent."}}
        r = hub.call("send_slack_notification", {"channel": p["channel"], "text": p["text"], "approval_id": p["action_id"]})
        r.pop("_meta", None)
        db.set_action(p["action_id"], status="executed" if r.get("ok") else "failed", result=r)
        return {"result": r, "trace": state["trace"] + [{"tool": "send_slack_notification", "server": "slack", "ok": r.get("ok"), "mode": r.get("mode"), "ms": None, "args": {"channel": p["channel"]}, "error": r.get("error")}]}

    def finalize(state: S) -> dict:
        r = state.get("result") or {}
        if r.get("ok"):
            msg = f"\n\n✅ **Slack confirmed delivery** to {state['proposed']['channel']} (ts {r['data'].get('ts')})."
        elif r.get("status") == "rejected":
            msg = "\n\n🚫 Action rejected – nothing was sent."
        else:
            msg = f"\n\n⚠️ **Not sent.** {r.get('error', 'Unknown error')}"
        return {"answer": state["answer"] + msg}

    def after_compose(state: S) -> str:
        if state["intent"] != "notify":
            return "end"
        urgent = [r for r in (ev(state, "calculate_stockout_risk") or []) if r["risk"] in ("Critical", "High")]
        return "propose" if urgent else "end"

    g = StateGraph(S)
    for name, fn in [("plan", plan), ("gather", gather), ("compose", compose), ("propose", propose), ("send", send), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.set_entry_point("plan")
    g.add_edge("plan", "gather"); g.add_edge("gather", "compose")
    g.add_conditional_edges("compose", after_compose, {"propose": "propose", "end": END})
    g.add_edge("propose", "send"); g.add_edge("send", "finalize"); g.add_edge("finalize", END)
    return g.compile(checkpointer=MemorySaver(), interrupt_before=["send"])

class SentinelAgent:
    def __init__(self):
        self.graph = build_agent()

    def _pack(self, cfg, query=None) -> dict:
        st = self.graph.get_state(cfg)
        v = dict(st.values)
        v["pending_approval"] = bool(st.next and "send" in st.next)
        return v

    def ask(self, query: str, thread_id: str, history: list | None = None) -> dict:
        cfg = {"configurable": {"thread_id": thread_id}}
        self.graph.invoke({"query": query, "history": history or []}, cfg)
        out = self._pack(cfg)
        iid = db.insert_id("INSERT INTO ai_investigations (query, intent, tools_used, answer, created_at) VALUES (:q,:i,:t,:a,:n)",
                           q=query, i=out.get("intent"), t=json.dumps([t["tool"] for t in out.get("trace", [])]), a=out.get("answer", ""), n=db.now())
        for r in out.get("recs", [])[:10]:
            db.execute("INSERT INTO recommendations (investigation_id, text, status, created_at) VALUES (:i,:t,'proposed',:n)", i=iid, t=r, n=db.now())
        return out

    def resume(self, thread_id: str, approved: bool, user: str = "user", role: str = "approver") -> dict:
        cfg = {"configurable": {"thread_id": thread_id}}
        st = self.graph.get_state(cfg).values
        if approved:
            db.approve_action(st["proposed"]["action_id"], user, role)  # raises PermissionError for non-approvers
        self.graph.update_state(cfg, {"approved": bool(approved)}, as_node="propose")
        self.graph.invoke(None, cfg)
        return self._pack(cfg)
