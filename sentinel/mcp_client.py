"""MCP client: discovers tools on every server and invokes them over Streamable HTTP (real MCP protocol, no direct function calls)."""
from __future__ import annotations
import asyncio, json, threading, time
from mcp import ClientSession
try:  # newer SDKs renamed the helper; keep both working
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client
from .config import LABELS, MODULES, env, server_url

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True, name="mcp-client-loop").start()

def _run(coro, timeout):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)

def _root(e: BaseException) -> str:
    while getattr(e, "exceptions", None):
        e = e.exceptions[0]
    return f"{type(e).__name__}: {e}"[:220]

async def _list_tools(url: str):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return (await s.list_tools()).tools

async def _call_tool(url: str, name: str, args: dict):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await s.call_tool(name, args)

def _parse(res) -> dict:
    text = "".join(getattr(c, "text", "") for c in res.content)
    if res.isError:
        return {"ok": False, "mode": "error", "error": text[:300] or "Tool error"}
    if getattr(res, "structuredContent", None):
        return res.structuredContent
    try:
        return json.loads(text)
    except Exception:
        return {"ok": False, "mode": "error", "error": "Unparseable tool response"}

class MCPHub:
    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._servers_ok: dict[str, str] = {}
        self._ts = 0.0
        self.timeout = float(env("MCP_TIMEOUT", "30"))

    def discover(self, force: bool = False) -> dict[str, dict]:
        """Dynamic tool discovery across all configured MCP servers."""
        if self._tools and not force and time.time() - self._ts < 60:
            return self._tools
        tools, status = {}, {}
        for key in MODULES:
            try:
                for t in _run(_list_tools(server_url(key)), 10):
                    tools[t.name] = {"server": key, "description": (t.description or "").strip().split("\n")[0],
                                     "schema": t.inputSchema or {}}
                status[key] = "up"
            except Exception as e:
                status[key] = f"down: {_root(e)}"
        self._tools, self._servers_ok, self._ts = tools, status, time.time()
        return tools

    def call(self, tool: str, args: dict | None = None, retries: int = 2) -> dict:
        """Invoke a tool through the MCP protocol with timeout + limited retries. Always returns the standard envelope plus _meta."""
        args = {k: v for k, v in (args or {}).items() if v is not None}
        t0 = time.time()
        info = self.discover().get(tool) or self.discover(force=True).get(tool)
        if not info:
            return {"ok": False, "mode": "error", "error": f"Tool '{tool}' is not available on any connected MCP server.",
                    "_meta": {"tool": tool, "server": None, "ms": 0, "args": args}}
        last = ""
        for attempt in range(retries + 1):
            try:
                res = _parse(_run(asyncio.wait_for(_call_tool(server_url(info["server"]), tool, args), self.timeout), self.timeout + 5))
                break
            except Exception as e:
                last = _root(e)
                time.sleep(0.5 * 2 ** attempt)
        else:
            res = {"ok": False, "mode": "disconnected", "error": f"MCP server '{info['server']}' unreachable after {retries + 1} attempts ({last})."}
        res["_meta"] = {"tool": tool, "server": info["server"], "ms": int((time.time() - t0) * 1000), "args": args}
        return res

    def health(self) -> list[dict]:
        self.discover(force=True)
        out = []
        for key in MODULES:
            row = {"key": key, "service": LABELS[key], "url": server_url(key), "server": self._servers_ok.get(key, "down"),
                   "tools": sorted(n for n, t in self._tools.items() if t["server"] == key)}
            if row["server"] == "up":
                r = self.call(f"{key}_status")
                row.update(mode=r.get("mode", "unknown"), ok=r.get("ok", False),
                           detail=(r.get("data") or {}).get("detail") or r.get("error", ""))
            else:
                row.update(mode="disconnected", ok=False, detail="MCP server not running")
            out.append(row)
        return out

_hub: MCPHub | None = None

def get_hub() -> MCPHub:
    global _hub
    if _hub is None:
        _hub = MCPHub()
    return _hub
