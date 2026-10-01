from __future__ import annotations

from collections import Counter
from math import sqrt
from typing import Any

from .metadata_store import MetadataStore, TableMetadata
from .utils import tokenize


DEFAULT_ALIASES = {
    '경제 분위기': ['경제심리지수', '경제심리', 'esi'],
    '기업 체감경기': ['기업경기실사지수', 'bsi', '기업경기'],
    '대출금리': ['예금은행 가중평균금리', '금리', '대출 이자'],
    '달러 가격': ['원 미달러 환율', '환율', '원달러'],
    '가계 빚': ['가계신용', '가계부채'],
    '집값': ['주택가격', '주택가격지수'],
}


class SearchEngine:
    def __init__(self, store: MetadataStore) -> None:
        self.store = store
        self.docs = self.store.available_supported_tables()
        self.doc_tokens = {m.table_id: tokenize(m.search_text) for m in self.docs}

    def _expand_query(self, query: str) -> list[str]:
        expanded = [query]
        for k, vals in DEFAULT_ALIASES.items():
            if k in query:
                expanded.extend(vals)
        return tokenize(' '.join(expanded))

    def _score(self, q_tokens: list[str], d_tokens: list[str]) -> float:
        if not q_tokens or not d_tokens:
            return 0.0
        q = Counter(q_tokens)
        d = Counter(d_tokens)
        common = set(q) & set(d)
        dot = sum(q[t] * d[t] for t in common)
        qn = sqrt(sum(v * v for v in q.values()))
        dn = sqrt(sum(v * v for v in d.values()))
        if qn == 0 or dn == 0:
            return 0.0
        overlap = len(common) / max(1, len(set(q_tokens)))
        return round((dot / (qn * dn)) * 0.8 + overlap * 0.2, 6)

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        q_tokens = self._expand_query(query)
        scored: list[tuple[float, TableMetadata]] = []
        for m in self.docs:
            score = self._score(q_tokens, self.doc_tokens.get(m.table_id, []))
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, m in scored[:top_k]:
            out.append({
                'table_id': m.table_id,
                'table_name': m.table_name,
                'score': round(score, 4),
                'path': m.path_text,
                'frequencies': m.frequencies,
                'start_period': m.start_period,
                'end_period': m.end_period,
            })
        return out
