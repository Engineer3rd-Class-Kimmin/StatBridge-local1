"""Start only this checkout's services; never kill an unrelated port owner."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'evaluation_runs' / 'portable_runtime.json'


def free_port(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def healthy(url: str) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def valid_keys() -> bool:
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / 'data/statbridge_mcp_server/.env')
    def present(name: str) -> bool:
        value = str(values.get(name) or '').strip()
        return bool(value) and not any(token in value.lower() for token in ('your_', 'replace', '여기에', '<', '>'))
    return present('KOSIS_API_KEY') and present('NCP_CLOVA_API_KEY')


def vector_ready() -> bool:
    path = ROOT / 'data/vector_store_349'
    if not (path / 'chroma.sqlite3').exists():
        return False
    try:
        import chromadb
        sys.path.insert(0, str(ROOT / 'tools'))
        from build_stat_vector_index import documents
        dictionary = json.loads((ROOT / 'src/agent/stat_dictionary/stat_language_dictionary.json').read_text(encoding='utf-8'))
        db = chromadb.PersistentClient(path=str(path))
        for name, expected in documents(dictionary).items():
            rows = db.get_collection(name).get(include=['documents'])
            actual = dict(zip(rows['ids'], rows['documents']))
            if actual != {row['id']: row['document'] for row in expected}:
                return False
        return True
    except Exception:
        return False


def stop() -> None:
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text(encoding='utf-8'))
    for service in state.get('services', []):
        pid = int(service['pid'])
        # Check creation time and executable as well as PID to avoid PID reuse.
        ps = f'$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}"; if($p){{$p | Select-Object ExecutablePath,CreationDate | ConvertTo-Json -Compress}}'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not result.stdout.strip():
            continue
        live = json.loads(result.stdout)
        if live == service.get('identity'):
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    STATE.unlink(missing_ok=True)


def start(api_port: int, ui_port: int, open_browser: bool, build_index: bool) -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / 'data/statbridge_mcp_server/.env', override=False)
    # Only previously recorded, still-identical processes belong to this launcher.
    stop()
    for port in (api_port, ui_port):
        if not free_port(port):
            raise RuntimeError(f'Port {port} is in use. Stop the other application first; no process was killed.')
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT / 'src/agent'), str(ROOT / 'data/statbridge_mcp_server')])
    env['STATBRIDGE_DATA_DIR'] = str(ROOT / 'data/runtime_data/processed')
    env['STATBRIDGE_TABLES_DIR'] = str(ROOT / 'data/runtime_data/tables')
    env['STATBRIDGE_VECTOR_PATH'] = str(ROOT / 'data/vector_store_349')
    env['STATBRIDGE_API_PORT'] = str(api_port)
    env['VITE_API_BASE_URL'] = '/api'
    if build_index:
        print('[SETUP] Checking/building 349-table vectors. First build calls the embedding API and can take several minutes.', flush=True)
        subprocess.run([sys.executable, str(ROOT / 'tools/build_stat_vector_index.py')], cwd=ROOT, env=env, check=True)
        subprocess.run([sys.executable, str(Path(__file__)), 'check-index'], cwd=ROOT, env=env, check=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    services: list[dict] = []
    commands = [
        ('agent', [sys.executable, '-m', 'uvicorn', 'bridge_api:app', '--host', '127.0.0.1', '--port', str(api_port)], ROOT / 'src/agent'),
        ('mcp', [sys.executable, str(ROOT / 'data/statbridge_mcp_server/server.py')], ROOT),
        ('frontend', ['cmd.exe', '/d', '/c', 'npm.cmd', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', str(ui_port), '--strictPort'], ROOT / 'src/agent/frontend'),
    ]
    try:
        for name, command, cwd in commands:
            with (STATE.parent / f'{name}.log').open('w', encoding='utf-8') as log:
                # A named pipe held by a hidden cmd keeps the stdio MCP server alive.
                if name == 'mcp':
                    command = [sys.executable, str(ROOT / 'scripts/windows/hold_mcp.py')]
                process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ps = f'Get-CimInstance Win32_Process -Filter "ProcessId={process.pid}" | Select-Object ExecutablePath,CreationDate | ConvertTo-Json -Compress'
            identity = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if process.poll() is not None or not identity.stdout.strip():
                raise RuntimeError(f'{name} exited; inspect evaluation_runs/{name}.log')
            services.append({'name': name, 'pid': process.pid, 'identity': json.loads(identity.stdout)})
            STATE.write_text(json.dumps({'services': services}, ensure_ascii=False, indent=2), encoding='utf-8')
        for name, url in [('Agent', f'http://127.0.0.1:{api_port}/api/health'), ('UI', f'http://127.0.0.1:{ui_port}/')]:
            deadline = time.monotonic() + 60
            while not healthy(url):
                if time.monotonic() > deadline:
                    raise RuntimeError(f'{name} failed to start; inspect evaluation_runs/*.log')
                time.sleep(.5)
            print(f'[OK] {name}: {url}', flush=True)
        if open_browser:
            webbrowser.open(f'http://127.0.0.1:{ui_port}/')
    except BaseException:
        stop()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'check-keys', 'check-index'])
    parser.add_argument('--api-port', type=int, default=8000)
    parser.add_argument('--ui-port', type=int, default=5173)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--build-index', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'check-index':
            raise SystemExit(0 if vector_ready() else 1)
        elif args.action == 'check-keys':
            raise SystemExit(0 if valid_keys() else 1)
        elif args.action == 'stop':
            stop()
        else:
            start(args.api_port, args.ui_port, not args.no_browser, args.build_index)
    except Exception as exc:
        # Do not print exception strings from API clients; they can contain keys.
        if isinstance(exc, RuntimeError):
            print(f'[ERROR] {exc}', file=sys.stderr)
        else:
            print(f'[ERROR] Setup failed ({type(exc).__name__}); check service logs.', file=sys.stderr)
        raise SystemExit(1)
