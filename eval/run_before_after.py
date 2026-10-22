"""Before(노트북 재현) / After(새 그래프) 를 같은 입력으로 돌려 score_results.py 입력 JSONL 을 만든다.

시나리오 (menu-before-after-eval 스킬 3절)
1. 단일 요청: queries.jsonl 의 문장마다 새 사용자로 1회 (seq 0).
2. 재방문: revisit 이 있는 문장은 같은 사용자로 한 번 더 (seq 1). 1회와 2회 사이에 프로세스를
   재시작한 것처럼 만든다. before 는 그래프를 새로 만들어 MemorySaver 가 비고, after 는 같은
   session.db·checkpoints.db 파일을 새 핸들로 다시 연다(MCP subprocess 도 새로 띄운다).
   2회째 행의 prev_recommended 에 1회째 추천을 넣는다.
HITL(approve_relax interrupt)은 자동 승인한다. latency 는 invoke + resume 전체 wall clock.
llm_calls 는 실제 LLM 호출 수다. openai 는 HTTP 요청 수(파싱 재시도 포함), fake 는 메서드 호출 수.

사용법 (프로젝트 루트에서):
    ./.venv/bin/python3 eval/run_before_after.py --llm fake                  # 파이프라인 확인, 과금 없음
    ./.venv/bin/python3 eval/run_before_after.py --llm openai --dry-run      # 예상 호출 수·토큰만 출력
    ./.venv/bin/python3 eval/run_before_after.py --llm openai --reps 2       # 실측 (과금)
옵션: --variants before,after[,after_rules]  --menu-repo mcp|local  --only case1,case3
      --control (before 를 재시작 없이 재방문한 before_norestart 행 추가)
결과: eval/results/{날짜}_{tag}_runs.jsonl (전체 행), _rep{n}_single.jsonl / _rep{n}_revisit.jsonl
      (score_results.py 입력), _summary.md. 같은 이름이 있으면 시각을 붙여 새 파일로 만든다.
"""
import argparse
import ast
import asyncio
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

EVAL = ROOT / "eval"
QUERIES = EVAL / "queries.jsonl"
RESULTS = EVAL / "results"
SCORER = ROOT / ".claude" / "skills" / "menu-before-after-eval" / "scripts" / "score_results.py"
MENUS_JSON = ROOT / "data" / "menus.json"
CONFTEST = ROOT / "tests" / "conftest.py"

MODEL = os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini")
QUALITY_MODEL = os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o-mini")
# 가정 단가(USD / 1M tokens, gpt-4o-mini). dry-run 비용 추정에만 쓴다 — 실측은 응답 usage 토큰.
PRICE_IN, PRICE_OUT = 0.15, 0.60
# fake 에서 출력 토큰을 셀 수 없어서 쓰는 추정치(op 별 응답 길이). dry-run 용.
EST_OUT_TOKENS = {"route": 15, "extract": 90, "recommend": 250, "review": 90}
# 실모델에서 한 요청이 칠 수 있는 최대 HTTP 호출 수(파싱 재시도 2배, 재추천 1회, 완화 2회 포함)
MAX_CALLS = {"before": 2 + 2 * 2, "after": 2 + 2 + 2 * 2 + 2, "after_rules": 2 + 2 + 2 * 2}


# ---------------------------------------------------------------- 입력·정답 라벨
def load_expected_block() -> dict:
    """tests/conftest.py 의 EXPECTED_BLOCK(사람이 적은 알레르기 정답표)을 import 없이 읽는다.
    conftest 는 import 시 env 를 fake 로 바꾸므로 실행하지 않고 그 dict 식만 평가한다."""
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "EXPECTED_BLOCK" for t in node.targets):
            return eval(compile(ast.Expression(node.value), str(CONFTEST), "eval"),
                        {"__builtins__": {}, "set": set})
    raise SystemExit("tests/conftest.py 에서 EXPECTED_BLOCK 을 찾지 못했다")


def load_queries(only: list[str] | None) -> list[dict]:
    qs = [json.loads(l) for l in QUERIES.read_text(encoding="utf-8").splitlines() if l.strip()]
    expected = load_expected_block()
    by_id = {q["id"]: q for q in qs}
    for q in qs:
        want = set().union(*(expected[t] for t in q["allergy_terms"])) if q["allergy_terms"] else set()
        if set(q["allergies"]) != want:
            raise SystemExit(f"{q['id']}: allergies 라벨 {q['allergies']} 이 EXPECTED_BLOCK 정답 {sorted(want)} 과 다름")
        if q["revisit"] and q["revisit"] not in by_id:
            raise SystemExit(f"{q['id']}: revisit 대상 {q['revisit']} 이 없음")
    if only:
        qs = [q for q in qs if q["id"] in only]
    return qs


# ---------------------------------------------------------------- 호출·토큰 계측
def est_tokens(text: str) -> int:
    """tiktoken 이 없어서 쓰는 대략값: 한글 1자 ≈ 1토큰, 그 밖 3.5자 ≈ 1토큰(보수적으로 크게)."""
    hangul = len(re.findall(r"[가-힣]", text))
    return hangul + (len(text) - hangul) // 3 + 1


class Meter:
    """프로세스 전역 카운터. 실행 1회 전후 snapshot 차이를 행에 적는다."""

    def __init__(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.by_op: dict[str, int] = {}

    def snap(self) -> tuple:
        return self.calls, self.prompt_tokens, self.completion_tokens

    def add(self, op: str, prompt_tokens: int, completion_tokens: int):
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.by_op[op] = self.by_op.get(op, 0) + 1


METER = Meter()


def install_http_meter():
    """agent/openai_llm.py 가 쓰는 httpx.post 를 이 프로세스에서만 감싸 실제 HTTP 호출과
    응답 usage(prompt/completion 토큰)를 센다. 요청 본문은 바꾸지 않는다."""
    import httpx
    import agent.openai_llm as mod

    real_post = httpx.post

    class _Proxy:
        def __getattr__(self, name):
            return getattr(httpx, name)

        def post(self, url, **kw):
            resp = real_post(url, **kw)
            try:
                u = resp.json().get("usage", {})
            except Exception:
                u = {}
            METER.add("http", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            return resp

    mod.httpx = _Proxy()


def fake_meter(op: str, system: str, user: str, model_cls):
    """fake 실행에서 openai provider 가 보냈을 프롬프트 크기를 잰다(dry-run 추정용)."""
    from agent import prompts
    full = f"{system}\n{prompts.json_instructions(model_cls)}" + user
    METER.add(op, est_tokens(full), EST_OUT_TOKENS.get(op, 80))


def metered_fake_llms(mode: str = "normal"):
    """After 용 fake LLM. 동작은 agent/fake_llm.py 그대로, 호출마다 METER 에 기록."""
    from agent import prompts
    from agent.fake_llm import FastLLM, QualityLLM
    from agent.schemas import Recommendation, ReviewResult, RouteResult, UserConstraints

    class MFast(FastLLM):
        def route(self, query):
            fake_meter("route", prompts.ROUTE_SYSTEM, query, RouteResult)
            return super().route(query)

        def extract(self, query):
            fake_meter("extract", prompts.EXTRACT_SYSTEM, query, UserConstraints)
            return super().extract(query)

        def review(self, items):
            fake_meter("review", prompts.REVIEW_SYSTEM, prompts.review_user(items), ReviewResult)
            return super().review(items)

    class MQuality(QualityLLM):
        def recommend(self, query, constraints, relaxed, candidates):
            fake_meter("recommend", prompts.RECOMMEND_SYSTEM,
                       prompts.recommend_user(query, constraints, relaxed, candidates), Recommendation)
            return super().recommend(query, constraints, relaxed, candidates)

    return (MFast("fast-model-stub", 0.00015, mode=mode),
            MQuality("quality-model-stub", 0.005, mode=mode))


def delta(before: tuple) -> dict:
    c, p, o = METER.snap()
    return {"llm_calls": c - before[0], "prompt_tokens": p - before[1], "completion_tokens": o - before[2]}


# ---------------------------------------------------------------- 행 만들기
def base_row(variant, rep, q, seq, user_id, asked, prev):
    return {"variant": variant, "rep": rep, "scenario": "single" if seq == 0 else "revisit",
            "case_id": asked["id"], "pair_id": q["id"], "user_id": user_id, "seq": seq,
            "query": asked["text"],
            "allergies": asked["allergies"], "budget": asked["budget"],
            "max_minutes": asked["max_minutes"], "meal": asked["meal"], "spicy_ok": asked["spicy_ok"],
            "prev_recommended": prev}


# ---------------------------------------------------------------- Before
def run_before(queries, all_by_id, llm_kind, rep, control):
    from baseline_notebook import FakeNotebookLLM, OpenAINotebookLLM, ask, build_notebook_graph, load_before_data

    if llm_kind == "openai":
        from agent.openai_llm import OpenAILLM
        llm = OpenAINotebookLLM(OpenAILLM(QUALITY_MODEL, usd_per_1k_tokens=0.005))
    else:
        llm = FakeNotebookLLM(meter=fake_meter)
    menus, alias = load_before_data()

    def one(graph, variant, q, seq, user_id, asked, prev):
        row = base_row(variant, rep, q, seq, user_id, asked, prev)
        snap, t0 = METER.snap(), time.perf_counter()
        try:
            s = ask(graph, asked["text"], user_id)
            row.update({
                "status": "done" if s["kept"] else "no_result",
                "relaxed": list(s.get("relaxed", [])),
                "kept": [{"name": k["name"], "reason": k["reason"]} for k in s["kept"]],
                "candidates": [m["name"] for m in s.get("candidates", [])],
                "dropped": [{"name": n, "why": w} for n, w in s.get("dropped", [])],
                "attempts": s.get("attempts", 0), "relax_count": s.get("relax_count", 0),
                "extracted": s.get("constraints", {}), "hitl_prompts": 0, "review_flags": [],
            })
        except Exception as e:
            row.update({"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}", "kept": []})
        row["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        row.update(delta(snap))
        return row

    rows = []
    proc_a = build_notebook_graph(llm, menus, alias)
    first = {}
    for q in queries:
        uid = f"r{rep}-before-{q['id']}"
        r = one(proc_a, "before", q, 0, uid, q, [])
        rows.append(r)
        first[q["id"]] = (uid, [k["name"] for k in r["kept"]])
    proc_b = build_notebook_graph(llm, menus, alias)      # 재시작: 새 MemorySaver
    for q in queries:
        if not q["revisit"]:
            continue
        uid, prev = first[q["id"]]
        asked = all_by_id[q["revisit"]]
        rows.append(one(proc_b, "before", q, 1, uid, asked, prev))
        if control:                                       # 재시작 없이(같은 프로세스) 재방문
            rows.append(one(proc_a, "before_norestart", q, 1, uid, asked, prev))
    return rows


# ---------------------------------------------------------------- After
class AfterProcess:
    """server.py lifespan 과 같은 조립. 같은 DB 파일로 다시 열면 '재시작' 이다."""

    def __init__(self, workdir: Path, llm_kind: str, review_mode: str, menu_repo: str):
        self.workdir, self.llm_kind, self.review_mode, self.menu_repo_kind = workdir, llm_kind, review_mode, menu_repo

    async def __aenter__(self):
        from contextlib import AsyncExitStack

        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        from agent.deps import SupervisorDeps
        from agent.graph import build_graph
        from agent.menu_repo import LocalMenuRepo, McpMenuRepo
        from agent.session_store import SqliteSessionStore

        self.stack = AsyncExitStack()
        db = self.workdir / "session.db"
        local = LocalMenuRepo(db_path=db)
        self.stack.callback(local.close)
        if self.menu_repo_kind == "mcp":
            from agent.mcp_client import MCPClient
            mcp = MCPClient(flaky_first_tool_call=False)
            self.stack.push_async_callback(mcp.stop)
            await mcp.start()
            repo = McpMenuRepo(mcp, fallback=local)
        else:
            repo = local
        saver = await self.stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(self.workdir / "checkpoints.db")))
        self.store = SqliteSessionStore(db_path=db)
        self.stack.callback(self.store.close)
        if self.llm_kind == "openai":
            from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
            fast = OpenAIFastLLM(MODEL, usd_per_1k_tokens=0.00015)
            quality = OpenAIQualityLLM(QUALITY_MODEL, usd_per_1k_tokens=0.005)
        else:
            fast, quality = metered_fake_llms()
        self.repo = repo
        self.graph = build_graph(SupervisorDeps(fast_llm=fast, quality_llm=quality, menu_repo=repo,
                                                session_store=self.store, review_mode=self.review_mode),
                                 checkpointer=saver)
        return self

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def ask(self, query: str, user_id: str):
        from langgraph.types import Command

        from agent.rules import RECENT_LIMIT
        from agent.state import initial_state

        config = {"configurable": {"thread_id": user_id}}
        recent = self.store.recent_recommendations(user_id, limit=RECENT_LIMIT)
        state = initial_state(query, user_id, self.store.load(user_id), recent)
        result = await self.graph.ainvoke(state, config)
        hitl = 0
        while result.get("__interrupt__") and hitl < 3:     # 승인 버튼을 누른 것으로 친다
            hitl += 1
            result = await self.graph.ainvoke(Command(resume={"approved": True}), config)
        self.store.append(user_id, "user", query)              # server.py 와 같이 대화 기록
        self.store.append(user_id, "assistant", result.get("final", ""))
        return result, hitl


def _relax_str(r: dict) -> str:
    return f"{r['label']} {r['from']}{r['unit']} → {r['to']}{r['unit']}"


async def run_after(queries, all_by_id, llm_kind, rep, variant, review_mode, menu_repo):
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{variant}-r{rep}-"))
    os.environ["MENU_DB_PATH"] = str(workdir / "session.db")   # MCP subprocess 의 meals 도 같은 파일

    async def one(proc, q, seq, user_id, asked, prev):
        row = base_row(variant, rep, q, seq, user_id, asked, prev)
        snap, t0 = METER.snap(), time.perf_counter()
        try:
            s, hitl = await proc.ask(asked["text"], user_id)
            orig = next((e["constraints"] for e in s.get("events", []) if e["type"] == "ConstraintsExtracted"), None)
            row.update({
                "status": "done" if s.get("kept") else ("chat" if s.get("route") == "chat" else "no_result"),
                "route": s.get("route"),
                "relaxed": [_relax_str(r) for r in s.get("relaxed", [])],
                "kept": [{"name": k["name"], "reason": k["reason"]} for k in s.get("kept", [])],
                "candidates": [m["name"] for m in s.get("candidates", [])],
                "dropped": s.get("dropped", []), "attempts": s.get("attempts", 0),
                "relax_count": s.get("relax_count", 0), "no_result_reason": s.get("no_result_reason"),
                "extracted": orig or {}, "final_constraints": s.get("constraints", {}),
                "hitl_prompts": hitl, "review_flags": s.get("review_flags", []),
                "llm_calls_reported": s.get("llm_calls", 0), "fallback": s.get("fallback", []),
                "mcp_fallbacks": proc.repo.drain_fallbacks(),
            })
        except Exception as e:
            row.update({"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}", "kept": []})
        row["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        row.update(delta(snap))
        return row

    rows, first = [], {}
    try:
        async with AfterProcess(workdir, llm_kind, review_mode, menu_repo) as proc_a:
            for q in queries:
                uid = f"r{rep}-{variant}-{q['id']}"
                r = await one(proc_a, q, 0, uid, q, [])
                rows.append(r)
                first[q["id"]] = (uid, [k["name"] for k in r["kept"]])
        async with AfterProcess(workdir, llm_kind, review_mode, menu_repo) as proc_b:   # 재시작
            for q in queries:
                if not q["revisit"]:
                    continue
                uid, prev = first[q["id"]]
                rows.append(await one(proc_b, q, 1, uid, all_by_id[q["revisit"]], prev))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return rows


# ---------------------------------------------------------------- 채점·요약
def score(path: Path) -> tuple[dict, str]:
    md = path.with_suffix(".md")
    out = subprocess.run([sys.executable, "-I", str(SCORER), str(path), str(MENUS_JSON), "--md", str(md)],
                         capture_output=True, text=True, check=True)
    table = md.read_text(encoding="utf-8")
    md.unlink()
    return json.loads(out.stdout), table


def extra_checks(rows) -> dict:
    """score_results.py 에 없는 보조 지표. 라벨 기준."""
    out = {}
    for v in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == v]
        ok = [r for r in rs if r["status"] != "error"]
        menus = {m["name"]: m for m in json.loads(MENUS_JSON.read_text(encoding="utf-8"))}
        extract_miss = [r["case_id"] for r in ok
                        if set(r["allergies"]) - set((r.get("extracted") or {}).get("allergies") or [])]
        spicy = [f'{r["case_id"]}:{k["name"]}' for r in ok if r["spicy_ok"] is False
                 for k in r["kept"] if k["name"] in menus and menus[k["name"]]["spicy"] >= 2]
        out[v] = {
            "runs": len(rs), "errors": len(rs) - len(ok),
            "allergy_extract_miss_runs": len(extract_miss),
            "spicy_violations": len(spicy),
            "hitl_prompts_total": sum(r.get("hitl_prompts", 0) for r in ok),
            "relaxed_runs": sum(1 for r in ok if r.get("relaxed")),
            # score_results.py 는 relaxed 가 적혀 있으면 초과를 '승인된 완화' 로 센다. before 는
            # 묻지 않고 풀었으므로(결과 첫 줄에 알리기만 함) 승인 없이 푼 실행을 따로 센다.
            "unapproved_relax_runs": sum(1 for r in ok if r.get("relaxed") and not r.get("hitl_prompts")),
            "review_flags_total": sum(len(r.get("review_flags") or []) for r in ok),
            "rerecommend_runs": sum(1 for r in ok if r.get("attempts", 0) >= 2),
            "llm_calls_total": sum(r["llm_calls"] for r in rs),
            "prompt_tokens_total": sum(r["prompt_tokens"] for r in rs),
            "completion_tokens_total": sum(r["completion_tokens"] for r in rs),
            "latency_ms_mean": round(statistics.mean(r["latency_ms"] for r in rs)) if rs else 0,
            "details": {"allergy_extract_miss": extract_miss, "spicy": spicy,
                        "errors": [f'{r["case_id"]}: {r["error"]}' for r in rs if r["status"] == "error"]},
        }
    return out


def extra_md(extra: dict) -> str:
    vs = list(extra)
    keys = [k for k in extra[vs[0]] if k != "details"]
    lines = ["| 보조 지표 | " + " | ".join(vs) + " |", "|---|" + "---|" * len(vs)]
    lines += [f"| {k} | " + " | ".join(str(extra[v][k]) for v in vs) + " |" for k in keys]
    for v in vs:
        for k, items in extra[v]["details"].items():
            if items:
                lines.append(f"\n**{v} / {k}**: " + ", ".join(items))
    return "\n".join(lines)


def unique_stem(date: str, tag: str) -> str:
    stem = f"{date}_{tag}"
    if any(RESULTS.glob(f"{stem}_*")):
        stem = f"{stem}_{datetime.now().strftime('%H%M%S')}"
    return stem


def write_results(rows, args, stem):
    RESULTS.mkdir(parents=True, exist_ok=True)
    runs_path = RESULTS / f"{stem}_runs.jsonl"
    runs_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    md = [f"# Before/After 채점 — {stem}", "",
          f"- 실행: `{' '.join(sys.argv)}`",
          f"- llm: {args.llm} ({'실측 아님, 파이프라인 확인용' if args.llm == 'fake' else f'{MODEL} / {QUALITY_MODEL}, temperature 0'})",
          f"- variants: {args.variants}, menu_repo(after): {args.menu_repo}, reps: {args.reps}, 문장 {len({r['pair_id'] for r in rows})}개",
          "- HITL 은 자동 승인. latency 는 invoke + resume 합. llm_calls 는 "
          + ("HTTP 호출 수(파싱 재시도 포함)." if args.llm == "openai" else "fake 메서드 호출 수."),
          "- 실패 행(status=error)은 채점 파일에서 빼고 보조 지표 errors 에 센다.",
          f"- 전체 행: `{runs_path.relative_to(ROOT)}`", ""]
    for rep in range(1, args.reps + 1):
        for scen, title in (("single", "단일 요청 (seq 0, 새 사용자)"), ("revisit", "재방문 (재시작 후 seq 1)")):
            part = [r for r in rows if r["rep"] == rep and r["scenario"] == scen]
            if not part:
                continue
            path = RESULTS / f"{stem}_rep{rep}_{scen}.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part if r["status"] != "error"),
                            encoding="utf-8")
            _, table = score(path)
            md += [f"## rep {rep} — {title}", f"입력: `{path.relative_to(ROOT)}`", "", table.strip(), "",
                   extra_md(extra_checks(part)), ""]
    summary = RESULTS / f"{stem}_summary.md"
    summary.write_text("\n".join(md) + "\n", encoding="utf-8")
    return runs_path, summary


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", choices=["fake", "openai"], default="fake")
    ap.add_argument("--variants", default="before,after")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--menu-repo", choices=["mcp", "local"], default="mcp")
    ap.add_argument("--only", default="")
    ap.add_argument("--control", action="store_true", help="before_norestart 행 추가")
    ap.add_argument("--dry-run", action="store_true", help="fake 로 돌려 openai 예상 호출 수·토큰만 출력")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        if v not in ("before", "after", "after_rules"):
            raise SystemExit(f"모르는 variant: {v}")

    all_by_id = {q["id"]: q for q in load_queries(None)}
    queries = load_queries([s for s in args.only.split(",") if s] or None)
    llm_kind = "fake" if args.dry_run else args.llm
    if llm_kind == "openai":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY 가 없다 (.env)")
        install_http_meter()
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ.pop("OPENAI_API_KEY", None)        # fake 실행이 실수로 과금되지 않게

    rows = []
    for rep in range(1, args.reps + 1):
        for v in variants:
            t0 = time.perf_counter()
            if v == "before":
                rows += run_before(queries, all_by_id, llm_kind, rep, args.control)
            else:
                rows += asyncio.run(run_after(queries, all_by_id, llm_kind, rep, v,
                                              "rules" if v == "after_rules" else "llm", args.menu_repo))
            n = [r for r in rows if r["rep"] == rep and r["variant"].startswith(v)]
            print(f"[rep {rep}] {v}: {len(n)}행, 오류 {sum(r['status'] == 'error' for r in n)}, "
                  f"{time.perf_counter() - t0:.1f}s", file=sys.stderr)

    if args.dry_run:
        print_dry_run(rows, args)
        return
    stem = unique_stem(datetime.now().strftime("%Y-%m-%d"), args.tag or args.llm)
    runs_path, summary = write_results(rows, args, stem)
    print(summary.read_text(encoding="utf-8"))
    print(f"\n결과: {runs_path.relative_to(ROOT)}\n요약: {summary.relative_to(ROOT)}", file=sys.stderr)


def print_dry_run(rows, args):
    print(f"# dry-run (fake 로 경로를 돌려 openai 호출을 추정) — 문장 {len({r['pair_id'] for r in rows})}개, reps {args.reps}")
    print("| variant | 실행 수 | 예상 호출 | 상한 호출 | 입력 토큰(추정) | 출력 토큰(추정) | 비용 USD(가정 단가) |")
    print("|---|---|---|---|---|---|---|")
    tot = [0, 0, 0, 0, 0.0]
    for v in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == v]
        calls = sum(r["llm_calls"] for r in rs)
        upper = len(rs) * MAX_CALLS.get(v.replace("_norestart", ""), 6)
        pin = sum(r["prompt_tokens"] for r in rs)
        pout = sum(r["completion_tokens"] for r in rs)
        usd = pin / 1e6 * PRICE_IN + pout / 1e6 * PRICE_OUT
        print(f"| {v} | {len(rs)} | {calls} | {upper} | {pin:,} | {pout:,} | {usd:.4f} |")
        for i, x in enumerate((calls, upper, pin, pout, usd)):
            tot[i] += x
    print(f"| 합계 | {len(rows)} | {tot[0]} | {tot[1]} | {tot[2]:,} | {tot[3]:,} | {tot[4]:.4f} |")
    print(f"\n호출 종류별(fake 기준): {METER.by_op}")
    print("예상 호출 = fake 경로 그대로. 실모델은 재추천(후보 밖·조건 위반 pick)과 파싱 재시도로 늘 수 있다. "
          "상한 = 실행 수 × 요청당 최대 HTTP 호출. 토큰은 한글 1자≈1토큰 대략값, 단가는 "
          f"gpt-4o-mini 입력 ${PRICE_IN}/1M·출력 ${PRICE_OUT}/1M 가정.")


if __name__ == "__main__":
    main()
