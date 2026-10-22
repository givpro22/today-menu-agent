"""Before/After 실행 결과를 같은 기준으로 채점한다.

입력 JSONL 한 줄 = 한 번의 추천 실행:
    {"variant": "before"|"after"|...,  "case_id": "case1", "user_id": "u1", "seq": 0,
     "allergies": ["밀"], "budget": 5000, "max_minutes": null, "meal": null,
     "relaxed": ["예산 5000원 → 8500원"],
     "kept": [{"name": "볶음밥", "reason": "..."}],
     "prev_recommended": ["된장찌개", ...],      # 이 실행 직전에 같은 사용자에게 추천된 메뉴 (재시작 포함)
     "candidates": ["된장찌개", ...],            # (선택) 이번 실행에서 LLM 에 준 후보 이름. 있으면 후보 밖 pick 을 센다
     "latency_ms": 1234, "llm_calls": 2}

budget/max_minutes 는 사용자가 원래 말한 값이다. relaxed 가 있으면 초과는 '승인된 완화' 로 따로 센다.

사용법:
    python3 -I score_results.py <results.jsonl> <menus.json> [--md out.md]
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# 이유 문장에 나온 표현 → 메뉴 데이터에서 참이어야 하는 조건
REASON_RULES = [
    ("맵", lambda m: m["spicy"] >= 2, "맵다고 했지만 spicy<2"),
    ("매운", lambda m: m["spicy"] >= 2, "맵다고 했지만 spicy<2"),
    ("얼큰", lambda m: m["spicy"] >= 2, "얼큰하다고 했지만 spicy<2"),
    ("국물", lambda m: "국물" in m["tags"], "국물이라 했지만 국물 태그 없음"),
    ("담백", lambda m: "기름진" not in m["tags"], "담백하다 했지만 기름진 태그"),
    ("차가", lambda m: "차가운" in m["tags"], "차갑다 했지만 차가운 태그 없음"),
]
NEGATIONS = ("않", "안 ", "없", "덜")
# "매운 맛이 강하다" 처럼 세기를 말하면 최고 맵기(3)여야 한다. 노트북 6-3 제육볶음(맵기 2) 사례.
INTENSIFIERS = ("강", "아주", "엄청", "화끈", "확실")


def reason_errors(reason, menu):
    errs = []
    for word, ok, msg in REASON_RULES:
        i = reason.find(word)
        if i < 0:
            continue
        window = reason[i:i + 8]
        if any(n in window for n in NEGATIONS):
            continue
        if not ok(menu):
            errs.append(msg)
        elif word in ("맵", "매운", "얼큰") and menu["spicy"] < 3 and any(s in window for s in INTENSIFIERS):
            errs.append("맵기가 강하다고 했지만 spicy<3")
    return list(dict.fromkeys(errs))


def score(rows, menus):
    by_name = {m["name"]: m for m in menus}
    acc = defaultdict(lambda: defaultdict(list))
    for r in rows:
        a = acc[r["variant"]]
        kept = r.get("kept", [])
        a["runs"].append(1)
        a["empty"].append(int(not kept))
        a["latency_ms"].append(r.get("latency_ms", 0))
        a["llm_calls"].append(r.get("llm_calls", 0))
        prev = set(r.get("prev_recommended", []))
        cands = set(r["candidates"]) if r.get("candidates") else None
        for k in kept:
            m = by_name.get(k["name"])
            a["picks"].append(1)
            if m is None:
                a["unknown_menu"].append(k["name"])
                continue
            if any(x in m["allergens"] for x in r.get("allergies", [])):
                a["allergy_violation"].append(f'{r["case_id"]}:{k["name"]}')
            over = (r.get("budget") is not None and m["price"] > r["budget"]) or (
                r.get("max_minutes") is not None and m["minutes"] > r["max_minutes"])
            if over:
                key = "over_limit_approved" if r.get("relaxed") else "over_limit_silent"
                a[key].append(f'{r["case_id"]}:{k["name"]}')
            if r.get("meal") in ("점심", "저녁") and "간식" in m["tags"]:
                a["meal_violation"].append(f'{r["case_id"]}:{k["name"]}')
            if cands is not None and k["name"] not in cands:
                a["off_candidate"].append(f'{r["case_id"]}:{k["name"]}')
            if k["name"] in prev:
                a["repeat"].append(f'{r["case_id"]}:{k["name"]}')
            for e in reason_errors(k.get("reason", ""), m):
                a["reason_error"].append(f'{r["case_id"]}:{k["name"]} ({e})')
    return acc


def summarize(acc):
    out = {}
    for v, a in acc.items():
        picks = max(len(a["picks"]), 1)
        out[v] = {
            "runs": len(a["runs"]),
            "picks": len(a["picks"]),
            "empty_result_runs": sum(a["empty"]),
            "allergy_violations": len(a["allergy_violation"]),
            "silent_over_limit": len(a["over_limit_silent"]),
            "approved_over_limit": len(a["over_limit_approved"]),
            "meal_violations": len(a["meal_violation"]),
            "unknown_menu": len(a["unknown_menu"]),
            "off_candidate": len(a["off_candidate"]),
            "repeat_rate": round(len(a["repeat"]) / picks, 3),
            "reason_error_rate": round(len(a["reason_error"]) / picks, 3),
            "latency_ms_median": statistics.median(a["latency_ms"]) if a["latency_ms"] else 0,
            "llm_calls_mean": round(statistics.mean(a["llm_calls"]), 2) if a["llm_calls"] else 0,
            "details": {k: a[k] for k in ("allergy_violation", "over_limit_silent", "off_candidate", "repeat", "reason_error") if a[k]},
        }
    return out


def to_markdown(summary):
    variants = list(summary)
    metrics = [k for k in summary[variants[0]] if k != "details"]
    lines = ["| 지표 | " + " | ".join(variants) + " |", "|---|" + "---|" * len(variants)]
    for k in metrics:
        lines.append(f"| {k} | " + " | ".join(str(summary[v][k]) for v in variants) + " |")
    for v in variants:
        for k, items in summary[v]["details"].items():
            lines.append(f"\n**{v} / {k}**: " + ", ".join(items))
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit(__doc__)
    rows = [json.loads(l) for l in Path(args[0]).read_text(encoding="utf-8").splitlines() if l.strip()]
    menus = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    summary = summarize(score(rows, menus))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if "--md" in args:
        Path(args[args.index("--md") + 1]).write_text(to_markdown(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
