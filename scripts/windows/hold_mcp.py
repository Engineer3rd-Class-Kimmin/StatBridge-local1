"""Own the MCP subprocess and hold its stdio input until the launcher stops it."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
process = subprocess.Popen([sys.executable, str(root / 'data/statbridge_mcp_server/server.py')], stdin=subprocess.PIPE)
raise SystemExit(process.wait())
