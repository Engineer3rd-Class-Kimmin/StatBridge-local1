"""Check the migrated checkout using a temporary real HTTP server."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_dotenv(ROOT / "data" / "statbridge_mcp_server" / ".env", override=False)
    if len(sys.argv) > 1:
        load_dotenv(sys.argv[1], override=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src" / "agent"), str(ROOT / "data" / "statbridge_mcp_server")])
    env["STATBRIDGE_VECTOR_PATH"] = str(ROOT / "data" / "vector_store_349")
    env["STATBRIDGE_DATA_DIR"] = str(ROOT / "data" / "runtime_data" / "processed")
    env["STATBRIDGE_TABLES_DIR"] = str(ROOT / "data" / "runtime_data" / "tables")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "bridge_api:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT / "src" / "agent", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    session = requests.Session()
    session.trust_env = False
    base = f"http://127.0.0.1:{port}/api"
    try:
        for _ in range(60):
            if server.poll() is not None:
                raise RuntimeError("temporary Agent API exited")
            try:
                health = session.get(base + "/health", timeout=1)
                if health.ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Agent API health timeout")
        response = session.post(base + "/query", json={"query": "최근 경제심리지수 추이 보여줘", "period_start": "2025-01-01", "period_end": "2025-12-31", "execute": True}, timeout=90)
        response.raise_for_status()
        query = response.json()
        result = {"health": health.json(), "query_status": query.get("status"), "execution_status": (query.get("debug") or {}).get("executionStatus"), "series_count": query.get("seriesCount"), "rows_preview": (query.get("debug") or {}).get("rowsPreview")}
        if query.get("status") == "need_output_config":
            output = session.post(base + "/output", json={"session_ids": [query["outputSessionId"]], "chart_type": "line"}, timeout=30)
            output.raise_for_status()
            rendered = output.json()
            result.update(output_status=rendered.get("status"), chart_series=len(rendered.get("chart") or []))
        destination = ROOT / "eval" / "runs" / "20261001_local_transfer_http.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
