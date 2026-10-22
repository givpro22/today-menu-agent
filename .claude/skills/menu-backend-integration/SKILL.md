---
name: menu-backend-integration
description: "오늘뭐먹지 Agent의 백엔드 연동 계층 구현 방법. mcp_server.py 의 MCP Tool/Resource/Prompt(메뉴 조회, 식사 기록), agent/mcp_client.py 연동, data/ 교체(menus.json, allergen_alias.json), agent/session_store.py 의 SQLite 스키마(사용자별 최근 추천·식사 기록·대화), server.py FastAPI 엔드포인트(추천, HITL 승인 resume, 먹었어요, 기록 조회)를 다룬다. 장애 진단용 모니터링·runbook 데이터 제거, MCP 도구 추가·변경, API 추가·수정, DB 스키마 변경, run 스크립트·.env 정리 작업 시 반드시 사용."
---

# 백엔드 연동 구현

도메인 규칙은 `menu-domain-spec`, 그래프 쪽 인터페이스는 `langgraph-menu-graph` 를 따른다. API 응답 shape 은 `_workspace/01_architect_contracts.md` 가 단일 원천이다. 계약에 없는 필드를 추가하면 프론트가 모르므로, 추가할 때는 계약 문서를 먼저 고치고 frontend-engineer 에게 알린다.

## 0. sample 관례 먼저
sample 의 mcp_server.py 가 어떤 MCP 라이브러리(FastMCP 등)와 transport 를 쓰는지, mcp_client.py 가 어떻게 세션을 열고 도구를 부르는지, server.py 가 그래프를 어떻게 호출하는지 읽고 그대로 따른다. web_demo_server.py 의 프록시 경로도 확인한다 — 새 엔드포인트가 프록시에 안 걸리면 브라우저에서 404 가 난다.

## 1. 데이터 교체
- 기존 장애 진단 데이터(모니터링 JSON, runbook md)는 삭제한다. 남겨 두면 MCP Tool 목록에 장애 도구가 섞여 시연 때 혼란스럽다. 삭제 목록은 `_workspace/03_platform_changes.md` 에 적는다.
- `data/menus.json`, `data/allergen_alias.json` 은 `menu-domain-spec/scripts/extract_notebook.py` 로 만든다. 손으로 옮기지 않는다.

## 2. MCP 설계
Tool 은 그래프가 실제로 부르는 것만 만든다. 안 쓰는 도구는 평가자 눈에 "장식" 이다.
| 종류 | 이름 | 역할 | 호출하는 곳 |
|---|---|---|---|
| Tool | `list_menus` | 전체 메뉴(또는 category 필터) | filter/verify 의 menu_repo |
| Tool | `get_menu` | 이름으로 1개 조회, 없으면 null | verify, review |
| Tool | `get_meal_history` | user_id 의 최근 식사 기록 | filter(최근 먹은 메뉴 제외) |
| Tool | `save_meal` | "먹었어요" 기록 | server.py 먹었어요 API |
| Resource | `menu://allergens` | 알레르기 표기 사전 | extract 정규화 |
| Prompt | 추천/리뷰 프롬프트 | sample 이 MCP Prompt 를 쓰면 동일 방식으로 | 선택 |

- 그래프는 MCP 를 직접 알지 않고 `menu_repo` 인터페이스(list/get)만 안다. MCP 구현과 JSON 직접 읽기 구현 두 개를 두면 pytest 에서 MCP 서버 없이 돈다(DI).
- MCP 호출 실패 시: 1회 재시도 → 로컬 JSON 폴백 → 로그. 메뉴 조회가 죽어서 추천 전체가 죽으면 안 된다. 단 폴백이 일어났다는 사실은 응답 meta 에 남긴다.

## 3. SQLite (session_store)
sample 의 대화 기록 테이블은 유지하고 다음을 추가한다.
```sql
recommendations(id, user_id, menu_name, created_at)      -- 최근 추천 6개 계산용
meals(id, user_id, menu_name, eaten_at)                  -- "먹었어요"
```
- 최근 추천 제외는 recommendations 기준 최근 6개, 식사 기록 제외는 meals 기준 최근 3일. 둘의 의미가 다르니 섞지 않는다.
- 체크포인터를 SqliteSaver 로 쓰면 같은 DB 파일을 쓰되 테이블 이름 충돌이 없는지 확인한다.
- DB 경로는 환경변수로. 테스트는 임시 파일 DB 를 쓴다.

## 4. API (server.py)
계약 문서 기준. 기본안:
| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/chat` (sample 경로 유지) | `{user_id, message}` | `{status:"done", kept[], relaxed[], summary, review_flags[], trace}` 또는 `{status:"needs_approval", proposal:{field, from, to}, thread_id}` |
| POST | `/chat/resume` | `{user_id, approved}` | `/chat` 와 같은 shape |
| POST | `/meals` | `{user_id, menu_name}` | `{ok}` |
| GET | `/meals?user_id=` | | `{items:[{menu_name, eaten_at}]}` |
| GET | `/health` | | sample 유지 |

- 일반 대화("안녕, 뭐 할 수 있어?")와 이름 기억 같은 sample 기능은 유지한다. sample 의 분류 노드가 있으면 "메뉴 추천 / 일반 대화" 분류로 바꿔 재사용한다.
- 입력 검증: 빈 메시지, 너무 긴 메시지(예: 500자), 대기 중인 interrupt 가 없는데 resume 호출 → 4xx 와 사람이 읽을 메시지. 이것들이 "예외 상황 테스트" 항목이 된다.
- LLM 키가 없거나 provider 가 죽으면 500 대신 명확한 오류 메시지. fake provider 로 서버가 뜨는지도 확인한다.

## 5. 실행 스크립트·환경
- `sh/run_agent_openai.sh`, `sh/run_agent_ollma.sh`(sample 오타 이름 유지), `sh/run_frontend.sh` 가 새 서비스로 그대로 뜨게 한다. fake provider 실행 스크립트를 하나 추가하면 시연 리허설에 쓸 수 있다.
- `.env.example` 에 새 변수(REVIEW_MODE, DB 경로 등)를 추가한다. 실제 키가 든 `.env` 는 절대 커밋하지 않는다.

## 6. 완료 기준
- fake provider 로 서버 기동 → curl 로 case1(done), case3(needs_approval → resume approved → done) 응답 확인. 실제 응답 JSON 을 `_workspace/03_platform_curl.md` 에 붙인다.
- 서버 재시작 후 같은 user_id 로 case1 재요청 시 직전 추천이 빠진다.
