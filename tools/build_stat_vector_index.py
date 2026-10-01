from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "src" / "agent"
sys.path.insert(0, str(AGENT))

from ncp_retrieval_client import NcpRetrievalClient  # noqa: E402


def compact(values, limit=180):
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def documents(dictionary):
    result = {"stat_concepts": [], "stat_tables": [], "stat_dimensions": []}
    for table in dictionary["tables"]:
        tid = str(table["table_id"])
        common = {"table_id": tid, "table_name": str(table.get("table_name") or tid)}
        concepts = compact([*table.get("domains", []), *table.get("aliases", []),
                            *table.get("semantic_aliases", []), *table.get("public_query_terms", [])])
        result["stat_concepts"].append({"id": f"concept:{tid}", "metadata": common,
            "document": f"통계표 {common['table_name']} | 개념 및 별칭: {'; '.join(concepts)}"})
        result["stat_tables"].append({"id": f"table:{tid}", "metadata": common,
            "document": f"통계표ID {tid} | 이름 {common['table_name']} | 주기 {table.get('prd_se','')} | "
                        f"항목 {'; '.join(compact(table.get('item_names', [])))} | 단위 {'; '.join(compact(table.get('units', [])))}"})
        dim_parts = []
        for dimension in table.get("dimensions", []):
            names = []
            for value in dimension.get("values", []):
                names.extend([value.get("normalized"), value.get("value_name"), *value.get("aliases", []),
                              *value.get("layman_terms", []), *value.get("related_concepts", [])])
            dim_parts.append(f"{dimension.get('dimension_name') or dimension.get('api_param')}: {'; '.join(compact(names))}")
        result["stat_dimensions"].append({"id": f"dimension:{tid}", "metadata": common,
            "document": f"통계표 {common['table_name']} | 분류값 " + " | ".join(dim_parts)})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--documents-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="smoke-test limit per collection; 0 builds all")
    parser.add_argument("--delay", type=float, default=1.05, help="seconds between uncached API calls")
    args = parser.parse_args()
    dictionary_path = AGENT / "stat_dictionary" / "stat_language_dictionary.json"
    data = json.loads(dictionary_path.read_text(encoding="utf-8"))
    store = Path(__import__("os").getenv("STATBRIDGE_VECTOR_PATH", str(ROOT / "data" / "vector_store")))
    manifest_dir = ROOT / "data" / "vector_documents"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    docs = documents(data)
    for name, items in docs.items():
        selected = items[: args.limit] if args.limit else items
        (manifest_dir / f"{name}.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.documents_only:
        print(json.dumps({"status": "documents_ready", "counts": {k: len(v) for k, v in docs.items()}}, ensure_ascii=False))
        return
    try:
        import chromadb
    except ImportError as exc:
        raise SystemExit("chromadb is required. Install statbridge_mcp_server/requirements.txt") from exc
    if args.rebuild and store.exists():
        shutil.rmtree(store)
    store.mkdir(parents=True, exist_ok=True)
    cache_path = manifest_dir / "embedding_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() and not args.rebuild else {}
    client = NcpRetrievalClient()
    if not client.configured:
        raise SystemExit("NCP API key is required for Embedding v2 index build")
    db = chromadb.PersistentClient(path=str(store))
    counts = {}
    for name, items in docs.items():
        selected = items[: args.limit] if args.limit else items
        collection = db.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
        for index, item in enumerate(selected, 1):
            digest = hashlib.sha256(item["document"].encode("utf-8")).hexdigest()
            vector = cache.get(digest)
            if vector is None:
                vector = client.embed(item["document"])
                cache[digest] = vector
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
                time.sleep(args.delay)
            collection.upsert(ids=[item["id"]], documents=[item["document"]], metadatas=[item["metadata"]], embeddings=[vector])
            print(f"{name}: {index}/{len(selected)}", flush=True)
        counts[name] = collection.count()
    print(json.dumps({"status": "ok", "path": str(store), "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
