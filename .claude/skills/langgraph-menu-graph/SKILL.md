---
name: langgraph-menu-graph
description: "sample 프로젝트의 agent/graph.py 와 LLM 모듈(openai_llm.py, ollama_llm.py, fake_llm.py)을 오늘뭐먹지 메뉴 추천 그래프로 재구성하는 방법. 노드(extract/filter/approve_relax/relax/recommend/verify/review/remember/no_result), 조건부 엣지와 루프 상한, interrupt() 기반 Human-in-the-loop 승인과 resume, 이유 검증 reviewer 노드(LLM-as-judge), LLM 의존성 주입, 노드 모듈 분리, LangSmith 추적을 다룬다. 그래프 노드 추가·수정, 프롬프트 변경, HITL, 체크포인터, reviewer, LLM provider 교체 작업 시 반드시 사용."
---

# 메뉴 추천 그래프 구현

도메인 규칙은 `menu-domain-spec` 스킬이 원천이다. 판정 기준이나 프롬프트 규칙을 여기서 새로 정하지 말고 그 스킬을 따른다.

## 0. 먼저 sample 을 읽는다
sample 의 graph.py 가 State, 노드 등록, LLM 주입, MCP 호출, 세션 연동을 어떻게 하는지 읽고 그 관례(함수 시그니처, 로깅, 설정 로딩, provider 선택 방식)를 유지한다. 구조를 새로 발명하면 server.py·fake_llm 과 경계가 어긋난다. 아키텍트가 정리한 `_workspace/01_architect_sample_map.md` 와 `_workspace/01_architect_contracts.md` 가 있으면 그것부터 읽는다.

## 1. 목표 그래프
```
START → extract → filter ─┬ 후보 있음 ─────────────→ recommend → verify ─┬ dropped & attempts<2 → recommend
                          ├ 후보 0, relax_count<2 → approve_relax ─┬ 승인 → relax → filter
                          │                                       └ 거절 → no_result
                          └ 후보 0, relax_count>=2 → no_result      verify ─ 그 외 → review → remember → END
no_result → END
```
- `approve_relax`: 다음에 풀 조건과 새 값을 **먼저 계산**해서 `interrupt({"type":"relax_approval","proposal":...})` 로 내보낸다. 사용자에게 "얼마로" 를 보여 줘야 승인 의미가 있다. relax 는 승인된 proposal 을 적용만 한다(계산 중복 금지).
- `review`: verify 를 통과한 kept 의 이유 문장이 메뉴 데이터 속성과 모순되는지 판정한다. 아래 4절.
- 노트북 그래프와 다른 점은 approve_relax, review 두 노드와 recommend 입력의 완화 칸뿐이다. 나머지는 baseline 을 그대로 옮긴다 — 변경이 적을수록 Before/After 차이를 설명하기 쉽다.

## 2. 모듈 분리 (기술 요소: 모듈화)
권장 배치. sample 관례와 충돌하면 sample 을 따르고 이유를 계약 문서에 적는다.
```
agent/
  graph.py        # build_graph(llm, menu_repo, checkpointer) 조립만
  state.py        # MenuState TypedDict
  rules.py        # block_reason, over_limit, match_score, normalize_allergens (순수 함수)
  nodes/          # extract.py, filter.py, relax.py, recommend.py, verify.py, review.py, remember.py
  prompts.py      # 추출/추천/리뷰 프롬프트
```
rules.py 는 LLM·IO 의존이 없어야 pytest 로 바로 검증된다.

## 3. 의존성 주입 (기술 요소: DI)
- `build_graph(llm, menu_repo, checkpointer)` 형태로 외부 의존을 인자로 받는다. 노드는 클로저나 `functools.partial` 로 의존을 받는다. 전역 `recommender` 를 바꿔 끼우던 노트북 방식(7-1)은 테스트에서 교체가 안 되므로 쓰지 않는다.
- llm 인터페이스는 세 메서드로 통일: `extract(user_input) -> UserConstraints`, `recommend(...) -> Recommendation`, `review(kept, menus) -> ReviewResult`. openai/ollama/fake 가 같은 인터페이스를 구현한다.
- fake_llm 은 **결정적**이어야 한다. 입력 문장의 키워드로 조건을 만들고, 후보 앞에서 3개를 고르는 식. 테스트용으로 "후보 밖 메뉴를 고르는" 모드, "이유에 틀린 속성을 쓰는" 모드를 플래그로 둔다 — verify 재추천 경로와 review 경로를 LLM 없이 통과시키기 위해서다(노트북 6-5 를 테스트로 승격).
- ollama 는 작은 모델(qwen3 0.6b 등)이 structured output 을 자주 깨뜨린다. 파싱 실패 시 1회 재시도 후 fake 규칙 기반 추출로 폴백하고 로그에 남긴다.

## 4. reviewer 노드 (기술 요소: LLM-as-judge)
- 입력: kept 각각의 reason + 해당 메뉴의 데이터(spicy, tags, main, allergens).
- 먼저 **코드 검사**: 이유에 "맵"이 있는데 spicy<2, "국물"이 있는데 국물 태그 없음 같은 사전 규칙. 싸고 결정적이다.
- 다음 **LLM 판정**: 코드 규칙에 안 걸린 나머지에 대해 "이유가 데이터와 모순되는가" 를 yes/no + 근거로. structured output.
- 모순이면 그 메뉴의 reason 을 데이터 기반 템플릿 문장으로 교체하고 `review_flags` 에 기록한다. 메뉴 자체를 빼지는 않는다(안전 문제는 verify 가 이미 처리).
- LLM 호출이 1회 늘어난다. `REVIEW_MODE=off|rules|llm` 환경변수로 끌 수 있게 해서 Before/After 에서 비용·지연 차이를 잰다. 이건 트레이드오프 설명의 핵심 숫자다.

## 5. HITL interrupt 와 resume
- 체크포인터가 있어야 interrupt 가 동작한다. 영속 저장은 `langgraph-checkpoint-sqlite` 의 `SqliteSaver` 를 우선 검토하고, 의존성 추가가 어려우면 sample 의 session_store 와 같은 DB 파일을 쓴다. 결정은 계약 문서에 적는다.
- thread_id = 사용자(또는 세션) id. 같은 thread 로 이어 불러야 recent 가 남는다.
- 실행 결과에 `__interrupt__` 가 있으면 API 는 `{"status":"needs_approval","proposal":...}` 로 응답하고, 승인/거절은 `Command(resume={"approved": bool})` 로 같은 thread 를 재개한다. 응답 shape 은 계약 문서가 정의하고 플랫폼 에이전트와 맞춘다.
- 매 요청마다 초기화할 값(relax_count, relaxed, attempts, dropped, kept, review_flags)과 이어받을 값(recent)을 구분한다. 노트북 ask() 처럼 recent 는 입력에 넣지 않는다.
- State 에 pydantic 객체를 넣지 않는다. `model_dump()` dict 만 둔다(노트북 11절 — 체크포인트 역직렬화 경고).

## 6. LangSmith
sample 이 LangSmith 를 쓰면 그 설정을 유지한다. 노드마다 `run_name` 이나 태그를 붙여 트레이스에서 approve_relax/review 경로가 보이게 한다. API 키는 .env 에만 둔다.

## 7. 완료 기준
- fake_llm 으로 case1~3 이 끝까지 돈다. case3 은 interrupt 에서 멈추고, 승인하면 예산 8500원 후보 3개, 거절하면 no_result.
- 후보 밖 메뉴 모드에서 verify→recommend 1회 후 remember 로 간다.
- 틀린 이유 모드에서 review_flags 가 생긴다.
- 변경 내역과 그래프 그림(mermaid)을 `_workspace/02_agent_graph_notes.md` 에 남긴다.
