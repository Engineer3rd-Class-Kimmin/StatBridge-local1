from __future__ import annotations
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
ROOT = Path(__file__).resolve().parent
os.environ.setdefault("STATBRIDGE_DATA_DIR", str(ROOT / "data/runtime_data/processed"))
os.environ["STATBRIDGE_HYBRID_RETRIEVAL"] = "0"
sys.path[:0] = [str(ROOT / "src/agent"), str(ROOT / "data/statbridge_mcp_server")]
from fastapi.testclient import TestClient
import bridge_api as api


class Tables349Tests(unittest.TestCase):
    def test_catalog_and_dictionary_have_same_349_tables(self):
        catalog = api.catalog()
        self.assertEqual(catalog["total"], 349)
        cards = [t for major in catalog["categories"] for middle in major["children"] for t in middle["children"]]
        self.assertEqual({t["tableId"] for t in cards}, set(api.agent.tables_by_id))
        for tid in ("DT_284Y001", "DT_284Y002"):
            table = next(t for t in cards if t["tableId"] == tid)
            self.assertEqual([len(d["valueOptions"]) for d in table["dimensions"]], [50, 14])
            self.assertEqual(table["units"], ["십억원"])

    def test_all_349_ids_resolve_and_have_valid_api_plans(self):
        for tid, table in api.agent.tables_by_id.items():
            with self.subTest(tid=tid):
                result = api.agent.resolver.resolve(tid)
                self.assertEqual(result["status"], "resolved")
                self.assertEqual(result["selected_table"]["table_id"], tid)
                plan = api.agent.build_api_plan(tid, result["selected_table"]).as_dict()
                self.assertIn(plan["item_id"], table["item_ids"])
                for dimension in table["dimensions"]:
                    self.assertIn(plan["classifications"][dimension["api_param"]], [v["value_id"] for v in dimension["values"]])

    def test_detailed_family_and_synonyms(self):
        for text, tid in [("상세자금순환표 잔액표", "DT_284Y001"), ("상세자금순환표 거래표", "DT_284Y002"),
                          ("상세 자금순환 금융거래", "DT_284Y002"), ("상세 자금순환 금융자산 부채 잔액", "DT_284Y001"),
                          ("상세자금순환 자산 부채 보유액", "DT_284Y001"), ("상세자금순환 순거래", "DT_284Y002")]:
            with self.subTest(text=text):
                result = api.agent.resolver.resolve(text)
                self.assertEqual(result["status"], "resolved")
                self.assertEqual(result["selected_table"]["table_id"], tid)
        result = api.agent.resolver.resolve("상세자금순환표")
        self.assertEqual(result["status"], "need_clarification")
        self.assertEqual(result["clarification_id"], "detailed_flow_of_funds_basis")

    def test_catalog_dimension_codes_survive_period_step(self):
        client = TestClient(api.app)
        table = api.agent.tables_by_id["DT_284Y001"]
        values = {d["api_param"]: d["values"][-1]["value_id"] for d in table["dimensions"]}
        response = client.post("/api/query", json={"query": table["table_id"], "dimension_values": values})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "need_period")
        self.assertEqual(data["state"]["_dimension_values"], values)
        with patch.object(api.service, "get_statistics", return_value={"source": "test", "status": "success", "row_count": 1,
                "rows": [{"PRD_DE": "2024", "DT": "12", "ITM_NM": "잔액표", "UNIT_NM": "십억원"}]}) as get_data:
            response = client.post("/api/query", json={"query": table["table_id"], "state": data["state"],
                                                       "period_start": "2024-01-01", "period_end": "2024-12-31"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "need_output_config")
        self.assertEqual(get_data.call_args.kwargs["classifications"], values)

    def test_invalid_dimension_code_is_rejected(self):
        client = TestClient(api.app)
        response = client.post("/api/query", json={"query": "DT_284Y001", "dimension_values": {"objL1": "invented"}})
        self.assertEqual(response.status_code, 400)

    def test_representative_defaults_are_validated_not_arbitrary(self):
        result = api.agent.resolver.resolve("DT_121Y007")
        plan = api.agent.build_api_plan("DT_121Y007", result["selected_table"]).as_dict()
        self.assertEqual(plan["classifications"]["objL1"], "13102134773ACC_ITEM.BEDBMA01")
        result = api.agent.resolver.resolve("DT_404Y017")
        plan = api.agent.build_api_plan("DT_404Y017", result["selected_table"]).as_dict()
        self.assertEqual(plan["classifications"]["objL1"], "DATA")


if __name__ == "__main__": unittest.main(verbosity=2)
