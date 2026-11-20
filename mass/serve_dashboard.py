from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if self.path.endswith((".html", ".css", ".js", ".json")):
            self.send_header("Cache-Control", "no-store, max-age=0")
        elif self.path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


class DashboardHTTPServer(ThreadingHTTPServer):
    # Avoid multiple Python processes silently sharing the same port (can cause random
    # connections to be accepted and then closed without a response).
    allow_reuse_address = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the SwarmBrain dashboard and runtime assets.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="Bind port. Default: 8000")
    args = parser.parse_args()

    handler = partial(DashboardRequestHandler, directory=str(ROOT_DIR))
    dashboard_index = ROOT_DIR / "dashboard" / "index.html"
    entry_path = "/dashboard/" if dashboard_index.exists() else "/"

    try:
        server = DashboardHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"Failed to bind http://{args.host}:{args.port} ({exc})", file=sys.stderr)
        print("Troubleshooting:", file=sys.stderr)
        print("  netstat -ano | findstr :8000", file=sys.stderr)
        print("  Get-CimInstance Win32_Process | ? { $_.ProcessId -eq <PID> } | fl ProcessId,CommandLine", file=sys.stderr)
        sys.exit(2)

    print(f"Dashboard is available at http://{args.host}:{args.port}{entry_path}")
    print(f"Serving files from {ROOT_DIR}")
    if dashboard_index.exists():
        print(f"Dashboard entry file: {dashboard_index}")
    else:
        print(f"Warning: {dashboard_index} not found. The URL will open directory listing instead.")

    py_exe = sys.executable
    print("Local self-test (bypass browser/proxy):")
    print(
        f'  "{py_exe}" -c "import urllib.request as u; print(u.urlopen(\'http://{args.host}:{args.port}{entry_path}\').status)"'
    )
    print(f"PID: {os.getpid()}")
    server.serve_forever()


if __name__ == "__main__":
    main()
