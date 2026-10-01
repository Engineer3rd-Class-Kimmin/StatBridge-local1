from __future__ import annotations

import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parent / "src" / "agent"
sys.path.insert(0, str(AGENT_ROOT))

from output_agent import OutputAgent


result = {
    "execution": {
        "status": "success",
        "rows": [
            {"PRD_DE": "202401", "DT": "100", "ITM_NM": "테스트", "UNIT_NM": "개"},
            {"PRD_DE": "202402", "DT": "110", "ITM_NM": "테스트", "UNIT_NM": "개"},
        ],
    }
}
output = OutputAgent().prepare(result, {
    "chart_type": "bar",
    "layout": "separate",
    "title": "편집 가능한 테스트 그래프",
    "show_legend": False,
})

assert output["status"] == "ready"
assert output["visualization"]["chartType"] == "bar"
assert output["visualization"]["layout"] == "separate"
assert output["visualization"]["editable"] is True
assert output["visualization"]["editOptions"]["title"] == "편집 가능한 테스트 그래프"
assert len(output["visualization"]["series"][0]["points"]) == 2
print("OUTPUT AGENT OK: chart selection, data shaping and edit specification")
