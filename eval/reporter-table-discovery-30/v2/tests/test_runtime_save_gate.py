"""An incomplete run must not publish a passing result."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'evaluate_runtime.py'
spec = importlib.util.spec_from_file_location('reporter30_runtime', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('total,passed,failed,cases', [
    (30, 29, 1, []),
    (29, 29, 0, []),
    (30, 30, 0, [{'passed': True, 'checks': {'http_success': True}}] * 29),
    (30, 30, 0, [{'passed': True, 'checks': {'http_success': False}}] * 30),
])
def test_failed_or_inconsistent_run_cannot_save_result(tmp_path, total, passed, failed, cases):
    result = {'summary': {'cases': total, 'passed': passed, 'failed': failed}, 'cases': cases}
    assert module.save_passing_result(tmp_path, result) is False
    assert list(tmp_path.iterdir()) == []
