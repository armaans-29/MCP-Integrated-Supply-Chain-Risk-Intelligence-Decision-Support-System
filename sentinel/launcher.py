"""Starts the MCP servers as separate processes (one per integration) and waits until they accept connections."""
from __future__ import annotations
import socket, subprocess, sys, time
from . import db
from .config import MODULES, ROOT, env, port

def port_open(key: str) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((env("MCP_HOST", "127.0.0.1"), port(key))) == 0

def ensure_servers(keys=None, wait: float = 40.0) -> dict:
    keys = list(keys or MODULES)
    db.init_db()  # create + seed once, before servers start
    (ROOT / "logs").mkdir(exist_ok=True)
    procs = {}
    for k in keys:
        if not port_open(k):
            log = open(ROOT / "logs" / f"{k}_server.log", "a")
            procs[k] = subprocess.Popen([sys.executable, "-m", MODULES[k]], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    end = time.time() + wait
    while time.time() < end and not all(port_open(k) for k in keys):
        time.sleep(0.4)
    return procs
