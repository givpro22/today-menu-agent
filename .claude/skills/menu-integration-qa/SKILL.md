---
name: menu-integration-qa
description: "오늘뭐먹지 Agent의 통합 QA 방법. 경계면 교차 비교(server.py 응답 ↔ web_chat.html 필드, graph State ↔ API, MCP Tool 이름 ↔ mcp_client 호출, interrupt payload ↔ resume 요청, SQLite 스키마 ↔ 조회 코드), fake_llm 기반 pytest 단위·그래프 테스트, 회귀 테스트 문장, 예외 상황 테스트를 다룬다. 테스트 작성·실행, 버그 확인, 동작 검증, 회귀 테스트, 오류·예외 상황 점검, '제대로 되는지 확인해줘' 요청 시 반드시 사용."
---

# 통합 QA

## 원칙: 양쪽을 동시에 읽는다
각 모듈이 따로 맞아도 연결 지점에서 어긋나는 버그가 가장 많다. "API 가 있는가" 가 아니라 "API 가 내보내는 필드를 화면이 같은 이름으로 읽는가" 를 본다. 모듈 하나가 끝날 때마다 바로 검사한다(incremental). 마지막에 몰아서 하면 경계면 버그가 다음 모듈로 번진다.

## 1. 경계면 체크리스트
| 생산자 | 소비자 | 확인할 것 |
|---|---|---|
| server.py 응답 dict | web_chat.html 의 `data.xxx` 접근 | 필드명·중첩·status 값 문자열이 계약과 일치. `_workspace/04_frontend_fields.md` 와 대조 |
| graph 최종 State | server.py 가 응답으로 옮기는 키 | kept/relaxed/summary/review_flags/trace 누락 없음 |
| `interrupt()` payload | server.py needs_approval 응답 → 프론트 승인 문구 → `/chat/resume` 요청 | proposal 필드(field/from/to) 이름이 세 곳에서 같음, resume 이 같은 thread_id 로 재개 |
| mcp_server.py `@tool` 이름·인자 | mcp_client.py / menu_repo 호출 | 이름 오타, 인자명, 반환 shape(list vs {items}) |
| session_store 테이블·컬럼 | 조회·저장 함수, `/meals` 응답 | 컬럼명 ↔ 응답 필드명 매핑 |
| web_demo_server.py 프록시 경로 | 프론트 fetch URL | 새 엔드포인트(/chat/resume, /meals)가 프록시에 등록됨 |
| `.env.example` | 코드의 os.getenv | 코드가 읽는 변수가 전부 example 에 있음 |
| rules.py | filter 와 verify | 둘이 같은 함수를 import (복붙 사본 금지) |

검사 방법: grep 으로 양쪽 문자열을 뽑아 집합 비교한다. 예) 서버 응답 키 집합 vs 프론트 `data\.(\w+)` 집합.

## 2. pytest
`tests/` 에 둔다. LLM 은 fake_llm 을 주입하고 DB 는 임시 파일. 네트워크 없이 돌아야 한다.
- **rules**: 알레르기 차단(밀 → 짜장면 차단), 표기 정규화(밀가루→밀), 맵기 차단, 점심/저녁 간식 차단, 예산·시간 over_limit.
- **relax**: case3 조건(5000원, 밀)에서 proposal 이 예산 → 8500원.
- **graph 경로**: 후보 있음 → done / 후보 0 → interrupt → 승인 → done / 거절 → no_result / 후보 밖 메뉴 fake 모드 → verify→recommend 1회 → remember / 틀린 이유 모드 → review_flags.
- **기억**: 같은 thread 두 번 → 직전 추천 제외. 체크포인터·DB 를 새로 열어도 유지(재시작 시뮬레이션).
- **API**: FastAPI TestClient 로 /chat, /chat/resume, /meals. 빈 메시지 4xx, 대기 interrupt 없이 resume 4xx.
- **안전 불변식**: 모든 테스트 응답에서 kept 의 어떤 메뉴도 사용자 알레르기를 포함하지 않는다. 헬퍼 assert 로 공통화.

## 3. 회귀 테스트 문장
노트북 문장(Before/After 에도 동일 사용):
- case1 `어제 술 먹고 치킨까지 먹어서 속이 안 좋아. 기름진 건 못 먹겠고 혼자 먹을 건데 만원 안쪽으로 20분 안에 되는 거 없을까`
- case1b (같은 사용자) `오늘도 속이 별로야. 어제처럼 만원 안쪽으로 20분 안에 먹을 수 있는 걸로 추천해 줘`
- case2 `친구 세 명이랑 저녁 먹는데 매운 게 당겨. 예산은 1인 15000원까지 괜찮고 시간은 넉넉해. 새우 알레르기 있는 친구가 있어`
- case3 `돈이 없어서 5000원 안쪽으로 먹어야 해. 근데 밀가루 알레르기 있어서 면이랑 빵은 못 먹어`
sample 기능 유지 확인: `안녕하세요. 어떤 일을 할 수 있나요?`, `제 이름은 민수입니다` → `제 이름이 뭐였죠?`
예외: 빈 문자열, 600자 문장, 메뉴와 무관한 질문, 존재하지 않는 알레르기 표기("고수 알레르기"), 모든 메뉴를 막는 조건(알레르기 다수 → no_result), LLM 키 없음.
문장 목록은 `doc/regression_test_queries.md` 를 메뉴용으로 바꿔 거기에 둔다.

## 4. 실제 LLM 확인
fake 로 다 통과한 뒤 openai provider 로 case1~3 을 1회씩 돌려 응답을 `_workspace/05_qa_live_runs.md` 에 남긴다. 과금이 있으므로 반복 실행하지 않는다. 실행 전 리더에게 확인받는다.

## 5. 리포트
`_workspace/05_qa_report.md`: 통과/실패/미검증을 구분하고, 실패는 `파일:라인 + 원인 + 수정 제안`. 경계면 이슈는 양쪽 담당 에이전트 모두에게 알린다. pytest 출력 요약(통과 수/실패 수)을 그대로 붙인다 — "다 통과" 같은 요약만 쓰지 않는다.
