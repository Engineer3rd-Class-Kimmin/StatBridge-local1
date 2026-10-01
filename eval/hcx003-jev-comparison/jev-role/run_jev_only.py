import argparse, json, os, time, math, urllib.request, urllib.error
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent

def load_env(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

def post(url, body, headers, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:
        return 0, {"error": str(e)}, time.perf_counter() - t0
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {"raw_text": raw}
    return status, parsed, time.perf_counter() - t0

def build_questions(lex):
    domain_labels = sorted(lex["concept_lexicon"])
    intents = sorted(lex["analysis_intent_lexicon"])
    quals = sorted(lex["qualifier_lexicon"])

    # IMPORTANT: Current TypeSafe API uses `criteria`, not `options`, for choice.
    qs = {
        "domain": {
            "type": "choice",
            "instructions": "사용자 질문의 핵심 통계 분야를 하나 선택하라. 다중 지표 질문이면 가장 먼저 언급된 지표의 분야를 선택하라.",
            "criteria": {
                k: lex["concept_lexicon"][k]["canonical"]
                for k in domain_labels
            },
        },
        "frequency": {
            "type": "choice",
            "instructions": "질문에 명시된 자료 주기를 선택하라. 명시되지 않았으면 NONE을 선택하라.",
            "criteria": {
                "M": "월별 또는 매달",
                "Q": "분기별",
                "Y": "연간, 연도별 또는 년별",
                "NONE": "질문에 자료 주기가 명시되지 않음",
            },
        },
        "series_count": {
            "type": "choice",
            "instructions": "질문에서 서로 독립적으로 검색해야 하는 명시적 통계 지표의 개수를 선택하라. 단일 지표는 0이다.",
            "criteria": {
                "0": "단일 지표이며 별도 series 분리가 필요 없음",
                "2": "독립적으로 검색할 지표가 2개",
                "3": "독립적으로 검색할 지표가 3개",
                "4": "독립적으로 검색할 지표가 4개",
                "5": "독립적으로 검색할 지표가 5개",
            },
        },
    }

    for k in intents:
        desc = lex["analysis_intent_lexicon"][k]
        # Noul is yes/no. Criteria are optional in the current API.
        qs["intent__" + k] = {
            "type": "noul",
            "instructions": f"사용자 질문에 다음 분석 의도가 명시적으로 포함되어 있는가? 의도={k}, 설명={desc}",
        }

    for k in quals:
        desc = lex["qualifier_lexicon"][k]
        qs["qual__" + k] = {
            "type": "noul",
            "instructions": f"사용자 질문에 다음 통계 조건 또는 기준이 명시적으로 포함되어 있는가? 조건={k}, 설명={desc}",
        }

    return qs

def jev_call(query, model, lex):
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        return {"status": 0, "latency_s": 0, "raw": {"error": "TYPESAFE_API_KEY missing"}, "answers": {}, "usage": {}}
    ep = os.getenv("TYPESAFE_ENDPOINT", "https://api.typesafe.ai").rstrip("/")
    body = {
        "state": query,
        "model": model,
        "questions": build_questions(lex),
    }
    status, parsed, latency = post(
        ep + "/v1/systemone",
        body,
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "StatBridge-Jev-Role-Eval/2.0",
        },
    )
    return {
        "status": status,
        "latency_s": latency,
        "raw": parsed,
        "answers": (parsed.get("answers") or {}) if isinstance(parsed, dict) else {},
        "usage": (parsed.get("usage") or {}) if isinstance(parsed, dict) else {},
        "request": body,
    }

def choice(answer):
    if not isinstance(answer, dict):
        return None
    return answer.get("choice")

def noul_yes(answer, threshold=0.5):
    if not isinstance(answer, dict):
        return False
    value = answer.get("noul")
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) >= threshold

def f1(pred, gold):
    p, g = set(pred), set(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    tp = len(p & g)
    precision = tp / len(p)
    recall = tp / len(g)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0

def score(case, pred):
    domain_ok = pred.get("domain") in set(case.get("expected_domains") or [])
    intent_score = f1(pred.get("intents") or [], case.get("expected_intents") or [])
    qual_score = f1(pred.get("qualifiers") or [], case.get("expected_qualifiers") or [])

    expected_freq = case.get("expected_frequency")
    freq_ok = None if expected_freq is None else pred.get("frequency") == expected_freq

    series_ok = int(pred.get("series_count") or 0) == int(case.get("expected_series_count") or 0)

    components = [
        1.0 if domain_ok else 0.0,
        intent_score,
        qual_score,
        1.0 if series_ok else 0.0,
    ]
    if freq_ok is not None:
        components.append(1.0 if freq_ok else 0.0)

    return {
        "domain_correct": domain_ok,
        "intent_f1": intent_score,
        "qualifier_f1": qual_score,
        "frequency_correct": freq_ok,
        "series_count_correct": series_ok,
        "role_score": sum(components) / len(components),
    }

def parse_prediction(answers, lex):
    f = choice(answers.get("frequency"))
    sc = choice(answers.get("series_count"))
    try:
        series_count = int(sc) if sc is not None else 0
    except Exception:
        series_count = 0

    return {
        "domain": choice(answers.get("domain")),
        "intents": sorted([
            k for k in lex["analysis_intent_lexicon"]
            if noul_yes(answers.get("intent__" + k))
        ]),
        "qualifiers": sorted([
            k for k in lex["qualifier_lexicon"]
            if noul_yes(answers.get("qual__" + k))
        ]),
        "frequency": None if f in (None, "NONE") else f,
        "series_count": series_count,
    }

def answer_schema_valid(answers, lex):
    if not isinstance(answers, dict) or not answers:
        return False
    if choice(answers.get("domain")) is None:
        return False
    if choice(answers.get("frequency")) is None:
        return False
    if choice(answers.get("series_count")) is None:
        return False
    for k in lex["analysis_intent_lexicon"]:
        a = answers.get("intent__" + k)
        if not isinstance(a, dict) or not isinstance(a.get("noul"), (int, float)) or isinstance(a.get("noul"), bool):
            return False
    for k in lex["qualifier_lexicon"]:
        a = answers.get("qual__" + k)
        if not isinstance(a, dict) or not isinstance(a.get("noul"), (int, float)) or isinstance(a.get("noul"), bool):
            return False
    return True

def percentile(values, q):
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

def summarize(rows, cases):
    n = len(rows)
    good = [r for r in rows if r["http_status"] == 200 and r["valid"]]
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case_id"]].append(json.dumps(r["pred"], ensure_ascii=False, sort_keys=True))

    freq_scores = [
        r["score"]["frequency_correct"]
        for r in rows
        if r["score"]["frequency_correct"] is not None
    ]
    lats = [r["latency_s"] for r in rows if r["http_status"] == 200]

    def avg(key):
        return 100 * sum(float(r["score"][key]) for r in rows) / n if n else 0.0

    return {
        "n_cases": len(cases),
        "n_runs": n,
        "http_200_pct": round(100 * sum(r["http_status"] == 200 for r in rows) / n, 2),
        "valid_output_pct": round(100 * sum(r["valid"] and r["http_status"] == 200 for r in rows) / n, 2),
        "api_error_pct": round(100 * sum(r["http_status"] != 200 for r in rows) / n, 2),
        "role_score_pct": round(avg("role_score"), 2),
        "domain_accuracy_pct": round(avg("domain_correct"), 2),
        "intent_f1_pct": round(avg("intent_f1"), 2),
        "qualifier_f1_pct": round(avg("qualifier_f1"), 2),
        "frequency_accuracy_pct": (
            round(100 * sum(bool(x) for x in freq_scores) / len(freq_scores), 2)
            if freq_scores else None
        ),
        "series_count_accuracy_pct": round(avg("series_count_correct"), 2),
        "consistency_pct": round(
            100 * sum(len(set(v)) == 1 for v in by_case.values()) / len(by_case), 2
        ) if by_case else None,
        "latency_p50_s": round(percentile(lats, 0.5), 3) if lats else None,
        "latency_p95_s": round(percentile(lats, 0.95), 3) if lats else None,
    }

def run_model(model, cases, lex, repeat, out_dir):
    rows = []
    delay = float(os.getenv("EVAL_DELAY_SECONDS", "0.3"))

    for rep in range(1, repeat + 1):
        for i, case in enumerate(cases, 1):
            res = jev_call(case["query"], model, lex)
            answers = res["answers"]
            pred = parse_prediction(answers, lex)
            valid = res["status"] == 200 and answer_schema_valid(answers, lex)
            sc = score(case, pred)

            row = {
                "model": model,
                "repeat": rep,
                "case_id": case["case_id"],
                "query": case["query"],
                "http_status": res["status"],
                "latency_s": res["latency_s"],
                "valid": valid,
                "pred": pred,
                "score": sc,
                "usage": res["usage"],
                "raw": res["raw"],
            }
            rows.append(row)

            err = ""
            if res["status"] != 200:
                err = " ERROR=" + json.dumps(res["raw"], ensure_ascii=False)[:300]
            print(
                f"{model} repeat {rep} {i}/{len(cases)} "
                f"status={res['status']} valid={valid} role={sc['role_score']:.3f}{err}",
                flush=True,
            )
            time.sleep(delay)

    path = out_dir / f"{model}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=None)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--env", default=".env")
    args = ap.parse_args()

    load_env(args.env)
    models = args.models or os.getenv("JEV_MODELS", "jev-latest,jev-preview")
    models = [m.strip() for m in models.split(",") if m.strip()]

    cases = [
        json.loads(line)
        for line in (ROOT / "goldset_statbridge_hcx003_role_100.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        cases = cases[:args.limit]

    lex = json.loads((ROOT / "canonical_lexicons.json").read_text(encoding="utf-8"))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "results" / f"jev_only_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = {}
    for model in models:
        rows = run_model(model, cases, lex, args.repeat, out_dir)
        summaries[model] = summarize(rows, cases)

    (out_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    headers = [
        "model", "role_score_pct", "domain_accuracy_pct", "intent_f1_pct",
        "qualifier_f1_pct", "frequency_accuracy_pct",
        "series_count_accuracy_pct", "valid_output_pct", "consistency_pct",
        "latency_p50_s", "latency_p95_s", "api_error_pct"
    ]
    lines = [
        "|" + "|".join(headers) + "|",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for model, s in summaries.items():
        lines.append("|" + "|".join(str(model if h == "model" else s.get(h)) for h in headers) + "|")
    (out_dir / "comparison_jev_only.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print("RESULT_DIR=" + str(out_dir))

if __name__ == "__main__":
    main()
