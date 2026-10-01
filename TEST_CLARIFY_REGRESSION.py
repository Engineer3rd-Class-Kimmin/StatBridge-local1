from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "src" / "agent"
sys.path.insert(0, str(AGENT))

from stat_dictionary.stat_language_resolver import StatLanguageResolver


def main() -> None:
    resolver = StatLanguageResolver(
        AGENT / "stat_dictionary" / "stat_language_dictionary.json"
    )

    household_loan = resolver.resolve("가계대출 자료")
    assert household_loan["status"] == "need_clarification", household_loan
    assert household_loan["clarification_id"] == "loan_measure", household_loan
    assert len(household_loan["options"]) >= 2, household_loan
    print("[PASS] 최초 가계대출 질의는 대출 자료 유형을 역질문")

    corporate_finance = resolver.resolve("기업 재무상태 좀 봐줘")
    assert corporate_finance["status"] == "need_clarification", corporate_finance
    assert corporate_finance["clarification_id"] == "corporate_finance_view", corporate_finance
    assert len(corporate_finance["options"]) >= 2, corporate_finance
    print("[PASS] 최초 기업 재무 질의는 재무 자료 유형을 역질문")

    broad_loan = resolver.resolve("대출 얼마나 늘었어?")
    assert broad_loan["clarification_id"] == "loan_type", broad_loan
    selected_loan_type = resolver.apply_clarification(
        broad_loan["state"],
        "loan_type",
        "산업별대출",
    )
    assert selected_loan_type.get("clarification_id") != "loan_measure", selected_loan_type
    print("[PASS] 앞 단계에서 산업별대출을 선택한 경우 기존 후보 제한을 유지")


if __name__ == "__main__":
    main()
