from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
AGENT_DIR = ROOT / "src" / "agent"
sys.path.insert(0, str(AGENT_DIR))

from jev_series_client import JevSeriesClient, JevSeriesError, JevSettings  # noqa: E402
from jev_series_hybrid import apply_jev_series_decision  # noqa: E402
from agent_runtime import StatBridgeAgent  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.last = None

    def post(self, url, **kwargs):
        self.last = (url, kwargs)
        if self.error:
            raise self.error
        return self.response


def settings(**overrides):
    values = dict(api_key="secret", endpoint="https://api.typesafe.ai", model="jev-preview", enabled=True, timeout=10)
    values.update(overrides)
    return JevSettings(**values)


class FakeNcp:
    configured = True

    class Settings:
        classifier_model = "HCX-003"

    settings = Settings()

    def __init__(self, classification):
        self.classification = classification

    def classify_stat_language(self, query):
        return copy.deepcopy(self.classification)


class FakeJev:
    def __init__(self, decision=None, error=None, enabled=True, key="secret"):
        self.settings = settings(enabled=enabled, api_key=key)
        self.decision = decision
        self.error = error
        self.calls = 0

    def classify_series_count(self, query, classification):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(self.decision)


class JevClientTests(unittest.TestCase):
    def test_request_uses_criteria_never_options(self):
        session = FakeSession(FakeResponse({"answers": {"series_count": {"choice": "2", "probabilities": {"2": 0.98}}}}))
        client = JevSeriesClient(settings(), session)
        result = client.classify_series_count("기준금리와 대출금리", {"concepts": ["기준금리", "대출금리"]})
        question = session.last[1]["json"]["questions"]["series_count"]
        self.assertIn("criteria", question)
        self.assertNotIn("options", question)
        self.assertEqual(result["series_count"], 2)
        self.assertEqual(result["probability"], 0.98)
        self.assertEqual(session.last[0], "https://api.typesafe.ai/v1/systemone")

    def test_bad_choice_is_rejected(self):
        session = FakeSession(FakeResponse({"answers": {"series_count": {"choice": "1"}}}))
        with self.assertRaises(JevSeriesError):
            JevSeriesClient(settings(), session).classify_series_count("질문", {})

    def test_timeout_is_wrapped(self):
        session = FakeSession(error=requests.Timeout("timeout"))
        with self.assertRaises(JevSeriesError):
            JevSeriesClient(settings(), session).classify_series_count("질문", {})


class SeriesRepairTests(unittest.TestCase):
    def apply(self, classification, count):
        return apply_jev_series_decision(classification, {"series_count": count, "probability": 0.9})

    def test_single_economic_sentiment_index(self):
        result, trace = self.apply({"concepts": ["경제심리지수"], "series": [{"label": "경제심리지수", "query": "경제심리지수"}]}, 0)
        self.assertEqual(result["series"], [])
        self.assertEqual(trace["series_action"], "cleared")

    def test_two_metrics_keeps_matching_hcx_series(self):
        series = [{"label": "기준금리", "query": "기준금리"}, {"label": "대출금리", "query": "대출금리"}]
        result, trace = self.apply({"concepts": ["기준금리", "대출금리"], "series": series}, 2)
        self.assertEqual(result["series"], series)
        self.assertEqual(trace["series_action"], "kept")

    def test_two_regions_one_metric_is_single(self):
        result, _ = self.apply({"concepts": ["청년 고용률"], "subjects": ["서울", "부산"], "series": [{"label": "서울", "query": "서울 청년 고용률"}, {"label": "부산", "query": "부산 청년 고용률"}]}, 0)
        self.assertEqual(result["series"], [])

    def test_three_metrics_are_repaired_from_hcx_concepts(self):
        hcx = {"normalized_query": "최근 3년 금리 비교", "concepts": ["기준금리", "예금금리", "대출금리"], "measures": ["금리"], "series": []}
        result, trace = self.apply(hcx, 3)
        self.assertEqual([x["label"] for x in result["series"]], ["기준금리", "예금금리", "대출금리"])
        self.assertEqual(trace["series_action"], "repaired")

    def test_three_regions_cpi_is_single(self):
        hcx = {"concepts": ["소비자물가지수"], "subjects": ["서울", "부산", "대구"], "series": [{"label": x, "query": f"{x} 소비자물가지수"} for x in ["서울", "부산", "대구"]]}
        result, _ = self.apply(hcx, 0)
        self.assertEqual(result["series"], [])

    def test_derived_growth_rate_is_single(self):
        hcx = {"concepts": ["소비자물가지수"], "measures": ["증가율"], "comparison_terms": ["전년 대비"], "series": [{"label": "소비자물가지수", "query": "소비자물가지수 증가율"}]}
        result, _ = self.apply(hcx, 0)
        self.assertEqual(result["series"], [])

    def test_unsafe_repair_preserves_hcx(self):
        hcx = {"concepts": ["서울", "부산"], "series": [{"label": "원본", "query": "원본"}]}
        result, trace = self.apply(hcx, 2)
        self.assertEqual(result["series"], hcx["series"])
        self.assertEqual(trace["series_action"], "not_safe")


class AgentFallbackTests(unittest.TestCase):
    def agent(self, jev):
        agent = StatBridgeAgent.__new__(StatBridgeAgent)
        agent.ncp = FakeNcp({"normalized_query": "기준금리 대출금리", "concepts": ["기준금리", "대출금리"], "measures": ["금리"], "series": []})
        agent.jev = jev
        return agent

    def test_success_adds_trace_and_repairs(self):
        _, result = self.agent(FakeJev({"series_count": 2, "probability": 0.97, "latency_ms": 12.3}))._classify("기준금리와 대출금리")
        self.assertEqual(len(result["series"]), 2)
        self.assertEqual(result["_jev_trace"]["final_series_count"], 2)

    def test_disabled_does_not_call_jev_and_preserves_hcx(self):
        jev = FakeJev(enabled=False)
        _, result = self.agent(jev)._classify("질문")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(result["series"], [])
        self.assertEqual(result["_jev_trace"]["jev_status"], "disabled")

    def test_missing_key_falls_back(self):
        jev = FakeJev(key="")
        _, result = self.agent(jev)._classify("질문")
        self.assertEqual(jev.calls, 0)
        self.assertTrue(result["_jev_trace"]["jev_fallback_used"])

    def test_endpoint_or_timeout_error_falls_back(self):
        jev = FakeJev(error=JevSeriesError("TypeSafe request failed"))
        _, result = self.agent(jev)._classify("질문")
        self.assertEqual(result["series"], [])
        self.assertEqual(result["_jev_trace"]["jev_status"], "error")
        self.assertTrue(result["_jev_trace"]["jev_fallback_used"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
