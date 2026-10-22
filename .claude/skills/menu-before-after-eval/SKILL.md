---
name: menu-before-after-eval
description: "오늘뭐먹지 Agent의 Before/After 실측 비교(보너스 10점), README.md 결과 보고서, 5분 발표 자료 구성 방법. 이전 노트북 구현(Before)과 새 서비스(After)를 같은 입력으로 돌려 알레르기 위반·재시작 후 중복률·이유 문장 오류율·무단 조건 초과·응답 시간·LLM 호출 수를 채점하고, 평가 기준(기능 30/기술 30/트레이드오프 20/발표 20) 순서에 맞춰 문서화한다. 성능 비교, 실측, 평가, 측정, 벤치마크, README 작성, 결과 보고서, 발표 자료, PPT, 한계·트레이드오프 정리 요청 시 반드시 사용."
---

# Before/After 실측과 결과 문서

평가는 "실제로 어떤 문제가 해결되었는지, 어떻게 검증했는지" 를 본다. 숫자는 반드시 실행해서 얻은 값만 쓴다. 측정 못 한 지표는 표에서 빼거나 "미측정" 으로 적는다 — 추정치를 실측처럼 쓰면 발표 질의에서 무너진다.

## 1. 비교 대상
| variant | 구현 | 설명 |
|---|---|---|
| before | `eval/baseline_notebook.py` | `menu-domain-spec/references/notebook_baseline.md` 코드를 그대로 옮긴 것. MemorySaver, HITL 없음, review 없음, 완화 입력 칸 없음 |
| after | 새 그래프 (`build_graph`) | SQLite 체크포인트, approve_relax 자동 승인, REVIEW_MODE=llm |
| after_rules (선택) | REVIEW_MODE=rules | reviewer 의 LLM 판정 비용을 분리해서 보여 줄 때 |

before 는 같은 모델(gpt-4o-mini, temperature 0)을 써야 비교가 공정하다. HITL 은 측정 스크립트에서 자동 승인으로 처리하고 그 사실을 문서에 적는다.

## 2. 입력 세트
`eval/queries.jsonl` — 20개 내외. 노트북 case1, case1b, case2, case3 을 포함하고 나머지는 조건 조합(알레르기 1~2개, 예산 빡빡/여유, 끼니 지정, 맵기 회피)을 고르게 섞는다. 각 줄에 사용자가 원래 말한 조건(allergies, budget, max_minutes, meal)을 **정답 라벨로** 적는다. LLM 추출값으로 채점하면 추출이 틀린 경우가 묻힌다. allergies 라벨은 재료 단위로 펼쳐 적는다(해산물 → 새우·오징어·조개). 해산물 알레르기 문장을 1~2개 넣으면 노트북 사전 버그가 Before 에서 숫자로 드러난다.

## 3. 측정 시나리오
1. **단일 요청**: 각 문장을 새 사용자로 1회.
2. **재방문**: 같은 사용자로 2회 요청. 1회와 2회 사이에 **프로세스를 재시작**(before 는 그래프 재생성 = MemorySaver 초기화, after 는 DB 유지). 2회째 결과의 `prev_recommended` 에 1회째 추천을 넣는다. 영속 메모리 효과가 숫자로 나오는 지점이다.
3. 모든 실행은 `score_results.py` 입력 형식의 JSONL 로 저장: `eval/results/{date}_{variant}.jsonl`.
4. latency 는 그래프 invoke 전후 wall clock, llm_calls 는 LLM 래퍼의 호출 카운터(또는 LangSmith 트레이스).
5. 같은 설정으로 2회 반복해 값이 흔들리면 두 값을 모두 적는다.

## 4. 채점
```
python3 -I .claude/skills/menu-before-after-eval/scripts/score_results.py eval/results/<file>.jsonl data/menus.json --md eval/results/<date>_summary.md
```
여러 variant 를 한 파일에 합쳐 넣으면 한 표로 나온다. 지표:
- allergy_violations — 둘 다 0 이어야 한다. 0 유지가 "안전 불변식이 리팩터링 후에도 지켜졌다" 는 증거.
- silent_over_limit vs approved_over_limit — 사용자 모르게 넘긴 것과 승인받고 넘긴 것을 구분.
- repeat_rate — 재방문 시나리오에서 직전 추천 중복 비율.
- reason_error_rate — 이유 문장 속 표현이 메뉴 데이터와 모순되는 비율. 규칙 기반 채점이라 놓치는 표현이 있다. 표본 몇 개를 사람이 직접 읽어 확인하고 그 결과도 적는다.
- latency_ms_median, llm_calls_mean — After 가 나빠지는 지표. 숨기지 말고 트레이드오프로 설명한다.

## 5. README.md 구성
과제 공지 권장 목차를 그대로 따른다:
1 프로젝트명·주제 선정 배경 (노트북에서 이어진 흐름) / 2 해결할 문제 (노트북 10절 한계 5개) / 3 개발 환경·기술 스택 (표) / 4 시스템 아키텍처 (Vue → FastAPI → LangGraph ↔ MCP, SQLite; mermaid 그래프) / 5 주요 기능 / 6 실행 방법 (setup_venv → .env → run_agent_openai → run_frontend → :8600, fake 모드 포함) / 7 테스트 결과·실행 화면 (pytest 요약, 회귀 문장 결과, 스크린샷) / 8 Before/After 표 / 9 문제점과 해결 (실제로 겪은 것만) / 10 한계와 향후 방향.
"sample 에서 재사용한 것 / 바꾼 것 / 새로 만든 것" 표와 주요 변경 파일 목록을 4~5절에 넣는다(과제 2 결과물 요구사항).

**문체 (사용자 요청: 거창하지 않게, AI 티 나지 않게).** 학생이 자기 프로젝트를 담담하게 정리한 글로 쓴다. 이전 노트북 마크다운의 문체(짧은 평서문 "~했다/~이다", 이유를 한 문장 덧붙이는 식)가 기준이다.
- 쓰지 않는 것: 이모지, 배지, "혁신적/강력한/완벽한/스마트한/원활한" 같은 수식어, "~를 통해 ~를 실현", "다양한", 모든 줄을 굵게, 같은 길이 불릿의 나열, 섹션마다 붙는 요약 문장, 목차에 없는 소개 문단.
- 쓰는 것: 실제 숫자와 파일 경로, 겪은 문제를 그대로("처음에는 ~였는데 ~때문이었다"), 표는 비교가 필요한 곳에만.
- 분량: 10개 목차를 지키되 각 절은 짧게. 설명이 길어지면 `doc/` 아래 파일로 빼고 링크만 둔다.
- 다 쓴 뒤 `humanize-korean` 스킬로 한 번 다듬는다(내용은 바꾸지 않고 문체만). 발표 슬라이드 문구에도 같은 기준을 적용한다.

## 6. 트레이드오프 (20점) — 스스로 짚을 것
- HITL: 승인 대기로 응답이 두 번 왕복. 승인 안 하고 떠나면 interrupt 된 thread 가 남는다(정리 정책).
- reviewer: LLM 호출 +1, 지연 증가. 규칙 기반 채점은 표현을 놓친다.
- SQLite: 단일 파일이라 동시 쓰기에 약하다. 사용자 식별이 브라우저 id 수준.
- MCP: 프로세스 경계가 하나 늘어 지연·장애 지점 증가 → 로컬 폴백으로 완화, 폴백 시 데이터 불일치 가능.
- 루프: relax 2회·재추천 1회 상한. 상한이 없으면 무한 루프 위험.
- RAG·Multi-Agent 를 넣지 않은 이유(메뉴 36개, 판단 지점 2곳) — 안 쓴 기술도 이유가 있으면 점수가 된다.

## 7. 발표 (5분)
가이드 순서: **문제 → 개선/추가 포인트 → Before/After 수치 → 한계 및 추가 개선**. 슬라이드 8~9장, 시연 1분 30초. 2장은 "왜 만들었나"(문제 상황·사용자·개발 목적)로 시작한다. 노트북 한계 표는 따로 두지 않는다(사용자 요청: 개발 이유가 먼저이고, 노트북과의 차이는 Before/After 장에서 보인다).
**필수 포함 5항목(과제 공지)** — 각 항목이 어느 슬라이드인지 한눈에 보이게 슬라이드 상단 작은 라벨로 표시한다:
1 프로젝트 주제 및 개발 목적 / 2 주요 기능 및 시스템 구성 / 3 코드 또는 기술적 특징 / 4 실제 실행 및 테스트 결과(실행 화면 + pytest 통과 수 + 회귀·스모크 결과) / 5 개선 성과와 한계점(Before/After 표 + 한계).
시연 동선: case3 입력 → 승인 버튼 → 카드 → 먹었어요 → 서버 재시작 → 같은 요청 → 직전 메뉴 빠짐. 네트워크 문제에 대비해 fake 모드 리허설과 스크린샷 백업을 준비한다.
산출물:
- `doc/presentation_outline.md` — 슬라이드별 제목·핵심 문장·들어갈 숫자.
- `doc/presentation.pdf` — PPT 를 PDF 로 내보낸 모양의 16:9 슬라이드 PDF. 간단하게: 슬라이드 6~8장, 장마다 제목 + 핵심 1문장 + 표/그림 하나. 원본은 `doc/presentation/slides.html`(한 장 = 1280×720 section)로 쓰고 headless Chrome 또는 Playwright 로 PDF 출력한다. 아키텍처 그림은 inline SVG/mermaid 렌더 결과, 실행 화면은 `_workspace/04_frontend_screens/` 스크린샷, Before/After 는 `eval/results/*_summary.md` 의 숫자를 그대로 쓴다. 출력 후 PDF 를 페이지별로 이미지로 열어 글자 잘림·겹침이 없는지 직접 확인한다.
