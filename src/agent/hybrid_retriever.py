from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ncp_retrieval_client import NcpRetrievalClient


class HybridStatRetriever:
    """Vector candidate generation + NCP rerank + deterministic dictionary scoring.

    Vector metadata is never trusted as an API plan. Returned table IDs are always
    intersected with the loaded dictionary catalog.
    """

    def __init__(self, resolver: Any, client: NcpRetrievalClient | None = None) -> None:
        self.resolver = resolver
        self.client = client or NcpRetrievalClient()
        # Use the verified 349-table store; the obsolete legacy store was
        # removed during cleanup after fresh-process retrieval checks passed.
        default_path = Path(__file__).resolve().parents[2] / "data" / "vector_store_349"
        self.path = Path(os.getenv("STATBRIDGE_VECTOR_PATH", str(default_path)))
        self.vector_top_k = int(os.getenv("STATBRIDGE_VECTOR_TOP_K", "24"))
        self.rerank_top_k = int(os.getenv("STATBRIDGE_RERANK_TOP_K", "12"))
        self.min_final = float(os.getenv("STATBRIDGE_MIN_FINAL_SCORE", "0.42"))
        self.min_gap = float(os.getenv("STATBRIDGE_MIN_SCORE_GAP", "0.06"))
        self.enabled = os.getenv("STATBRIDGE_HYBRID_RETRIEVAL", "1").lower() not in {"0", "false", "off"}
        self._collections: list[Any] | None = None

    def _load(self) -> list[Any]:
        if self._collections is not None:
            return self._collections
        self._collections = []
        if not self.enabled or not self.path.exists():
            return self._collections
        try:
            import chromadb
            db = chromadb.PersistentClient(path=str(self.path))
            for name in ("stat_concepts", "stat_tables", "stat_dimensions"):
                try:
                    self._collections.append(db.get_collection(name))
                except Exception:
                    pass
        except Exception:
            pass
        return self._collections

    def rank(self, query: str, confirmed: dict[str, str] | None = None, top_k: int = 12) -> list[dict[str, Any]]:
        rule = self.resolver.rank(query, confirmed=confirmed, top_k=max(self.vector_top_k, top_k))
        rule_by_id = {str(item["table_id"]): item for item in rule}
        if len(rule)>=1:
            first=float(rule[0].get("score") or 0)
            second=float(rule[1].get("score") or 0) if len(rule)>1 else 0.0
            exact=any(str(reason) in {"table_id","table_name","exact_table_phrase"} or str(reason).startswith("confirmed:") for reason in rule[0].get("reasons",[]))
            fast_score=float(os.getenv("STATBRIDGE_FAST_RULE_SCORE","150"))
            fast_gap=float(os.getenv("STATBRIDGE_FAST_RULE_GAP","60"))
            if exact and first>=fast_score and first-second>=fast_gap:
                for item in rule:
                    item.update({"vector_score":0.0,"rerank_score":0.0,"rule_score":item["score"],
                                 "confirmed_bonus":0.0,"negative_penalty":0.0,"final_score":min(1.0,float(item["score"])/220.0),
                                 "retrieval_path":"rule_fast_path"})
                return rule[:top_k]
        collections = self._load()
        if not collections or not self.client.configured:
            for item in rule:
                item.update({"vector_score": 0.0, "rerank_score": 0.0, "rule_score": item["score"],
                             "confirmed_bonus": 0.0, "negative_penalty": 0.0, "final_score": item["score"]})
            return rule[:top_k]

        try:
            query_vector = self.client.embed(query)
            vector_scores: dict[str, float] = {}
            docs: dict[str, str] = {}
            for collection in collections:
                result = collection.query(query_embeddings=[query_vector], n_results=self.vector_top_k,
                                          include=["documents", "metadatas", "distances"])
                for doc, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
                    table_id = str((meta or {}).get("table_id") or "")
                    if table_id not in self.resolver.tables_by_id:
                        continue
                    score = max(0.0, 1.0 - float(distance))
                    if score > vector_scores.get(table_id, -1.0):
                        vector_scores[table_id] = score
                        docs[table_id] = str(doc)
            ids = sorted(vector_scores, key=vector_scores.get, reverse=True)[: self.rerank_top_k]
            reranked = self.client.rerank(query, [{"id": table_id, "doc": docs[table_id]} for table_id in ids])
        except Exception:
            return rule[:top_k]

        candidates: list[dict[str, Any]] = []
        for table_id in set(rule_by_id) | set(vector_scores):
            if table_id not in self.resolver.tables_by_id:
                continue
            base = dict(rule_by_id.get(table_id) or self.resolver.candidate_shell(table_id))
            rule_score = float(base.get("score") or 0.0)
            rule_norm = min(1.0, rule_score / 220.0)
            vector_score = vector_scores.get(table_id, 0.0)
            rerank_score = reranked.get(table_id, 0.0)
            confirmed_bonus = 0.12 if any(str(r).startswith("confirmed:") for r in base.get("reasons", [])) else 0.0
            negative_penalty = float(base.get("negative_penalty") or 0.0)
            final = 0.34 * vector_score + 0.28 * rerank_score + 0.38 * rule_norm + confirmed_bonus - negative_penalty
            base.update({"vector_score": round(vector_score, 6), "rerank_score": round(rerank_score, 6),
                         "rule_score": round(rule_score, 2), "confirmed_bonus": confirmed_bonus,
                         "negative_penalty": negative_penalty, "final_score": round(final, 6),
                         "score": round(final * 220.0, 2)})
            candidates.append(base)
        candidates.sort(key=lambda item: (-item["final_score"], item["table_id"]))
        return candidates[:top_k]

    def confident(self, candidates: list[dict[str, Any]]) -> bool:
        if not candidates:
            return False
        top = float(candidates[0].get("final_score") or 0.0)
        second = float(candidates[1].get("final_score") or 0.0) if len(candidates) > 1 else 0.0
        return top >= self.min_final and top - second >= self.min_gap
