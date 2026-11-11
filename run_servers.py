"""Run all MCP servers in the foreground:  python run_servers.py"""
import signal, sys, time
from sentinel.launcher import ensure_servers, port_open
from sentinel.config import MODULES, server_url

procs = ensure_servers()
for k in MODULES:
    print(f"{'✔' if port_open(k) else '✘'} {k:<10} {server_url(k)}")
def stop(*_):
    for p in procs.values():
        p.terminate()
    sys.exit(0)
signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
print("MCP servers running – Ctrl+C to stop")
while True:
    time.sleep(2)
