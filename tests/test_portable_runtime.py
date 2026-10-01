import importlib.util
import socket
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portable_runtime', ROOT / 'scripts/windows/portable_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_blank_and_placeholder_keys_are_rejected(tmp_path):
    env = tmp_path / 'data/statbridge_mcp_server/.env'
    env.parent.mkdir(parents=True)
    with patch.object(runtime, 'ROOT', tmp_path):
        for content in ['', 'KOSIS_API_KEY=\nNCP_CLOVA_API_KEY=\n', 'KOSIS_API_KEY=your_key\nNCP_CLOVA_API_KEY=replace_me\n']:
            env.write_text(content, encoding='utf-8')
            assert not runtime.valid_keys()
        env.write_text('KOSIS_API_KEY="test-kosis"\nNCP_CLOVA_API_KEY="test-ncp"\n', encoding='utf-8')
        assert runtime.valid_keys()


def test_busy_port_is_not_treated_as_available():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        assert not runtime.free_port(sock.getsockname()[1])


def test_download_includes_all_launcher_dependencies():
    for path in ['START_STATBRIDGE.cmd', 'STOP_STATBRIDGE.cmd', 'scripts/windows/portable_runtime.py',
                 'scripts/windows/hold_mcp.py', 'data/statbridge_mcp_server/.env.example',
                 'data/statbridge_mcp_server/requirements.txt', 'data/runtime_data/processed/bok_table_master.csv',
                 'src/agent/frontend/package-lock.json', 'tools/build_stat_vector_index.py']:
        assert (ROOT / path).is_file()
