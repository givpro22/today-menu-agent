---
name: menu-agent-orchestrator
description: "오늘뭐먹지 메뉴 추천 AI Agent 미니 프로젝트(sample all_total_vue_student 기반 과제 2 신규 서비스)의 전체 진행을 조율하는 오케스트레이터. sample 분석 → 계약 설계 → 그래프·백엔드·화면 병렬 구현 → 모듈별 QA → Before/After 실측 → README·발표 개요까지 에이전트를 순서대로 호출한다. '프로젝트 진행해줘', '미니 프로젝트', '오늘뭐먹지', '메뉴 추천 에이전트 만들어', 'sample 이식', '다음 단계 진행' 요청 시 반드시 사용. 후속 작업: 특정 단계만 다시, 그래프만 다시, 화면만 수정, QA 재실행, 실측 다시, README 업데이트, 발표 자료 보완, 이전 결과 기반 개선 요청에도 이 스킬을 사용."
---

# 오늘뭐먹지 Mini-project 오케스트레이터

## 실행 모드: 서브 에이전트
이 환경에는 TeamCreate/TaskCreate 가 없어서 에이전트 팀을 만들 수 없다. 대신 리더(메인 세션)가 `Agent` 도구로 에이전트를 호출하고, 결과는 `_workspace/` 파일로 주고받는다. 같은 에이전트에게 후속 지시를 할 때는 새로 띄우지 않고 `SendMessage` 로 이어 간다(맥락 유지). 모든 Agent 호출에 `model: "opus"` 를 명시한다.

## 에이전트 구성
| 에이전트 | subagent_type | 스킬 | 주 산출물 |
|---|---|---|---|
| menu-architect | menu-architect | menu-domain-spec, langgraph-menu-graph, menu-backend-integration, menu-vue-ui | `_workspace/01_architect_*` |
| agent-engineer | agent-engineer | menu-domain-spec, langgraph-menu-graph | agent/, `_workspace/02_agent_graph_notes.md` |
| platform-engineer | platform-engineer | menu-domain-spec, menu-backend-integration | data/, mcp_server.py, server.py, session_store, `_workspace/03_platform_*` |
| frontend-engineer | frontend-engineer | menu-vue-ui | web_chat.html, `_workspace/04_frontend_*` |
| qa-inspector | qa-inspector | menu-integration-qa | tests/, `_workspace/05_qa_*` |
| eval-reporter | eval-reporter | menu-before-after-eval | eval/, README.md, doc/presentation_outline.md |

에이전트 프롬프트에는 역할 설명을 다시 쓰지 말고 "할 일 + 읽을 파일 경로 + 쓸 파일 경로 + 해당 스킬 이름" 만 넣는다. 역할은 `.claude/agents/*.md` 가 담는다.

## 일정 기준
Day1 17:00 진행 계획 등록 / Day3 13:00 GitHub·보고서 제출 / Day3 14:00~ 발표(본인 15:29). 남은 시간이 부족하면 Phase 3 의 선택 기능(after_rules variant, 식사 기록 사이드바)을 먼저 뺀다. 필수는 계획 9항목 중 1~5, 7, 9.

## Phase 0: 컨텍스트 확인
1. 프로젝트 루트에 sample(`all_total_vue_student*.zip` 또는 풀린 폴더)이 있는지 확인. **없으면 중단**하고 사용자에게 Slack 에서 받아 루트에 두라고 요청한다. sample 없이 구조를 추측해 구현하지 않는다.
2. `_workspace/` 확인:
   - 없음 → 초기 실행, Phase 1.
   - 있음 + 부분 요청("화면만", "QA 다시") → 해당 Phase 의 에이전트만 호출. 이전 산출물 경로를 프롬프트에 넣는다.
   - 있음 + 처음부터 다시 → `_workspace/` 를 `_workspace_{YYYYMMDD_HHMMSS}/` 로 옮기고 Phase 1.
3. 각 Phase 산출물 파일 존재로 어디까지 진행됐는지 판단하고 다음 Phase 부터 이어 간다.

## Phase 1: 준비 (리더 직접)
1. sample zip 을 새 빈 디렉토리에 풀고 내용을 확인한 뒤 프로젝트 루트로 옮긴다. 기존 README.md 와 충돌하면 sample 쪽을 `doc/` 아래로 둔다.
2. `.gitignore`: `.env`, `.venv/`, `*.db`, `__pycache__/`, `_workspace*/`, `AI 서비스 Mini-Project.pdf`(재배포 금지 문구), sample zip. 이름이 든 노트북·PDF 처리 방식은 사용자 결정을 따른다(Before 근거로 `doc/prior_work/` 권장).
3. sample 원본 상태로 첫 커밋을 제안한다 — Before 비교와 "무엇을 바꿨나" diff 의 기준점이 된다. 커밋은 사용자가 승인하면 한다.
4. `bash sh/setup_venv.sh` 후 sample 원본이 뜨는지 확인한다(가능하면 fake 또는 openai). 원래 동작 확인은 과제 STEP 1 이다.

## Phase 2: 설계
`Agent(subagent_type="menu-architect", model="opus")` — sample map, 계약, task split 작성.
완료 후 리더가 계약 문서를 읽고 사용자에게 요약(바뀌는 파일 수, API 목록, 삭제할 장애 진단 파일)을 보고한다. 계약 확정 전에 구현을 시작하지 않는다 — 계약이 흔들리면 세 구현이 모두 다시 고쳐진다.

## Phase 3: 병렬 구현
한 메시지에서 세 에이전트를 `run_in_background: true` 로 동시에 호출한다.
- agent-engineer: 그래프·LLM. menu_repo 는 JSON 구현으로 먼저.
- platform-engineer: data/MCP/DB/API. 그래프는 build_graph 인터페이스만 의존.
- frontend-engineer: 계약 예시 응답으로 먼저, curl 결과가 생기면 실제 호출로.
파일 소유권은 `_workspace/01_architect_task_split.md` 를 따른다. 같은 파일 동시 수정은 금지 — 경계에 걸린 파일(server.py 의 그래프 호출부 등)은 task split 에 소유자 한 명만 둔다.

## Phase 4: 모듈별 QA (incremental)
구현 에이전트 하나가 끝날 때마다 바로 `Agent(subagent_type="qa-inspector", model="opus")` 로 그 모듈과 그 모듈의 경계면을 검사한다.
- 실패 항목은 소유 에이전트에게 `SendMessage` 로 돌려보내 수정 → QA 재검증. **모듈당 수정 왕복 최대 2회.** 그래도 남으면 리포트에 남은 실패로 적고 사용자에게 알린다.
- 세 모듈이 다 끝나면 통합 QA 1회(전체 pytest + 회귀 문장 + 예외 상황).
- 실제 OpenAI 스모크 테스트는 과금이 있으므로 사용자 확인 후.

## Phase 5: 실측과 문서
1. `Agent(subagent_type="eval-reporter", model="opus")` 에 먼저 측정 계획과 예상 호출 수만 받는다 → 사용자 확인 → 측정 실행.
2. 알레르기 위반·무단 초과가 나오면 문서화를 멈추고 Phase 4 로 되돌린다.
3. README.md, doc/presentation_outline.md, doc/presentation.pdf(발표용 슬라이드 PDF) 작성. 리더가 PDF 페이지를 직접 열어 확인한다.

## Phase 6: 정리
1. 사용자에게 보고: 완료 항목(계획 9항목 기준), 테스트 통과 수, Before/After 핵심 숫자, 남은 실패·미측정.
2. 커밋 단위를 제안한다(설계/그래프/백엔드/화면/테스트/실측·문서). 사용자 승인 시 커밋, push 는 별도 확인.
3. `_workspace/` 는 지우지 않는다(감사 추적, README 근거).
4. 피드백을 묻고, 반영 내용을 CLAUDE.md 변경 이력에 기록한다.

## 데이터 흐름
```
[리더] Phase1 sample 준비
   ↓
menu-architect → 01_sample_map / 01_contracts / 01_task_split
   ↓ (계약 확정)
 ┌ agent-engineer ──→ agent/ + 02_notes ─┐
 ├ platform-engineer → server/MCP/DB + 03_curl ─┼→ qa-inspector (모듈마다) → 05_qa_report
 └ frontend-engineer → web_chat + 04_fields ─┘        ↑ 실패 → SendMessage 로 소유자에게
   ↓ (통합 QA 통과)
eval-reporter → eval/results + README + presentation_outline
```

## 에러 핸들링
| 상황 | 대응 |
|---|---|
| sample zip 없음 | Phase 0 에서 중단, 사용자에게 요청 |
| 에이전트 실패 | 같은 지시로 1회 재시도. 재실패 시 리더가 원인 파악 후 사용자에게 보고, 그 산출물 없이 진행 가능한지 판단 |
| 계약 위반 발견 | 계약이 틀렸으면 menu-architect 로 계약 수정(버전 올림) → 영향받는 에이전트에 SendMessage. 구현이 틀렸으면 소유자에게 |
| 파일 소유권 충돌 | 나중에 쓴 쪽 변경을 되돌리지 말고, 리더가 diff 를 보고 소유자에게 병합 지시 |
| ollama 가 느림 | 공지대로 openai(3-2) 로 전환. 개발 중에는 fake provider |
| 과금 걱정 | 개발·테스트는 fake, openai 는 스모크와 실측에만 |

## 테스트 시나리오
### 정상 흐름
1. 사용자: "프로젝트 진행해줘" (sample zip 이 루트에 있음)
2. Phase 0: `_workspace/` 없음 → 초기 실행
3. Phase 1: sample 풀기, .gitignore, 첫 커밋 제안, 원본 기동 확인
4. Phase 2: 계약 문서 3개 생성 → 사용자에게 요약
5. Phase 3: 세 에이전트 병렬 → Phase 4: 모듈별 QA, 실패 1건 → 소유자 수정 → 재검증 통과
6. Phase 5: 측정 계획 승인 → 측정 → README·발표 개요
7. 결과: fake 모드에서 case3 이 승인 버튼까지 동작, pytest 전부 통과, Before/After 표

### 에러 흐름
1. 사용자: "프로젝트 진행해줘" 인데 zip 이 없다
2. Phase 0 에서 중단하고 "Slack 에서 all_total_vue_student_refactoring.zip 을 받아 루트에 두세요" 라고 요청
3. 이후 Phase 3 에서 frontend-engineer 가 `proposal.to` 를 읽는데 서버가 `proposal.new_value` 를 보냄
4. qa-inspector 가 경계면 불일치로 보고 → 계약은 `to` → platform-engineer 에 수정 지시 → 재검증 통과
5. 리포트에 수정 이력 기록
