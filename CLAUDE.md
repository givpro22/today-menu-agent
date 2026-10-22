## 하네스: 오늘뭐먹지 메뉴 추천 AI Agent (Mini-project 과제 2)

**목표:** sample(all_total_vue_student) 구조 위에 이전 노트북의 오늘뭐먹지 그래프를 이식하고, 계획 9항목(MCP 데이터, 그래프, HITL, SQLite 메모리, 이유 검증, 모듈화/DI/테스트, Vue UI, Before/After)을 구현·검증·문서화한다.

**트리거:** 이 프로젝트의 구현·수정·테스트·실측·문서 작업 요청 시 `menu-agent-orchestrator` 스킬을 사용하라. 단순 질문은 직접 응답 가능.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-10-22 | 초기 구성 (에이전트 6, 스킬 7) | 전체 | - |
| 2026-10-22 | 발표 PDF(doc/presentation.pdf) 산출물 추가 | skills/menu-before-after-eval, agents/eval-reporter | 사용자 요청: PPT 를 PDF 로 만든 발표 자료 |
| 2026-10-22 | README·발표 문체 기준 추가 (담백하게, AI 티 없이) | skills/menu-before-after-eval | 사용자 요청 |
| 2026-10-22 | 모든 에이전트에 git 스테이징·커밋 금지 원칙 추가 | agents/* | platform-engineer 의 git rm 이 하네스 커밋에 섞임 |
| 2026-10-22 | 알레르기 상위 개념 펼치기 규칙, 정답 라벨 독립 원칙 | skills/menu-domain-spec, menu-before-after-eval | QA 회차 2 해산물 알레르기 누출 |
| 2026-10-22 | 발표 필수 5항목(주제·목적 / 기능·구성 / 기술 특징 / 실행·테스트 / 성과·한계) | skills/menu-before-after-eval | 사용자 요청: 과제 공지 발표 포함 항목 |
