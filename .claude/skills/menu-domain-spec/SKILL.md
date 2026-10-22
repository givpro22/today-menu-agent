---
name: menu-domain-spec
description: "오늘뭐먹지 메뉴 추천 Agent의 도메인 규칙 단일 원천(SSOT). 조건 스키마(UserConstraints), 알레르기·맵기·끼니·예산·시간 판정 기준, 조건 완화(relax) 규칙, 추천 프롬프트 규칙, 메뉴 데이터 스키마, 이전 노트북에서 배운 교훈을 담는다. 메뉴 추천 로직·프롬프트·메뉴 데이터·알레르기 판정·조건 완화를 구현하거나 수정하거나 검증할 때, 노트북 코드를 sample 프로젝트로 이식할 때, Before 구현이 무엇이었는지 확인할 때 반드시 이 스킬을 먼저 읽을 것."
---

# 오늘뭐먹지 도메인 규칙

에이전트들이 같은 기준으로 판정해야 결과가 어긋나지 않는다. filter 와 verify 가 다르게 판정하면 알레르기 메뉴가 새어 나가므로, 판정 기준은 이 문서와 `references/notebook_baseline.md` 의 함수 하나로만 정의한다.

## 서비스 한 줄
사용자가 식사 상황을 한 문장으로 적으면 조건에 맞는 메뉴를 최대 3개, 이유와 함께 추천한다.

## 역할 분담 원칙 (노트북에서 검증됨)
| 일 | 누가 | 이유 |
|---|---|---|
| 문장 → 조건 객체 | LLM (`with_structured_output`) | 자연어 해석은 LLM 몫 |
| 알레르기·맵기·끼니·최근추천·예산·시간 판정 | 코드 | 정답이 정해진 사실이라 LLM에 맡기면 틀릴 자리만 는다 |
| 후보 안에서 3개 고르고 이유 쓰기 | LLM | |
| 결과 검증(verify) | 코드, 항상 마지막 | LLM 이 무엇을 고르든 verify 를 거치지 않고는 결과에 못 들어간다 |
| 완화 내역 문장 | 코드가 `relaxed` 를 읽어 출력 | 7-1 교훈: LLM 이 "조건을 풀었다"고 거짓으로 썼다 |

이 분업을 깨는 변경(예: LLM 에게 알레르기 판정 맡기기)은 하지 않는다. 필요해 보이면 리더에게 먼저 보고한다.

## 조건 스키마 `UserConstraints`
budget(1인 상한, 원), max_minutes(분), spicy_ok(bool|None), avoid_keywords, prefer_keywords, allergies, people(기본 1), meal(아침/점심/저녁/간식|None).
필드 description 이 JSON Schema 로 모델에 넘어가므로 단위와 "언급 없으면 null" 을 description 에 적는다. 프롬프트에 같은 말을 반복하지 않는다.
추출 직후 `normalize_allergens` 로 표기를 데이터에 맞춘다(밀가루→밀, 달걀→계란 등). 이 사전은 `data/allergen_alias.json`.

## 판정 함수 두 개
- `block_reason(menu, c, recent)` — **풀지 않는 조건**: 알레르기, 맵기(spicy_ok False 이면 spicy>=2 차단), 끼니(점심/저녁에 간식 태그 차단), 최근 추천.
- `over_limit(menu, c)` — **relax 가 풀 수 있는 조건**: 예산, 시간.
- `match_score` — prefer 일치 +1, avoid 일치 −1, 매운 거 원하면 spicy>=2 에 +1. 상위 10개를 후보로.

filter 와 verify 는 반드시 이 두 함수를 공유한다. 모듈화할 때 한 파일(`agent/rules.py` 권장)에 두고 양쪽에서 import 한다.

## 조건 완화 규칙
- 순서: 예산 먼저(relax_count==0), 다음 시간. 최대 2회. 그 뒤에도 없으면 no_result.
- 새 값: block_reason 을 통과한 메뉴 가격(또는 시간)을 정렬해 3번째 값(부족하면 마지막). 후보가 최소 3개 생기도록.
- 알레르기·맵기·끼니는 어떤 경로로도 풀지 않는다. 건강 문제이거나 사용자가 분명히 말한 조건이다.
- 무엇을 얼마로 풀었는지 `relaxed` 목록에 남기고 결과 첫 줄에 코드가 출력한다.
- **이번 프로젝트 변경점**: relax 를 적용하기 전에 `interrupt()` 로 사용자 승인을 받는다(HITL). 거절하면 no_result 로 간다.

## 추천 프롬프트 규칙 (노트북 7-3 최종본)
1 후보 목록 안에서만 / 2 서로 다른 최대 3개 / 3 후보가 적으면 있는 만큼 / 4 충돌 시 알레르기>예산>시간>선호 / 5 이유는 메뉴 속성 하나와 사용자 상황 연결, 문장 복붙 금지 / 6 예산·시간은 추출 조건 값 기준 / 7 이유와 summary 에 숫자 쓰지 않기.
**이번 프로젝트 변경점**: 완화 내역을 입력 칸으로 되살린다(노트북 10절 — 된장찌개 8500원이 빠진 원인). 규칙 7 은 유지한다.

## 루프 상한
relax 2회, 재추천(verify→recommend) 1회. 그래프에 루프를 추가하면 반드시 상한을 State 카운터로 둔다. 무한 루프는 트레이드오프 설명에서 짚을 항목이다.

## 기억
최근 추천은 사용자별 6개(두 번 전 요청까지). 계속 쌓으면 후보가 바닥난다. 이번 프로젝트에서 SQLite 로 영속화한다.

## 메뉴 데이터 스키마 (`data/menus.json`)
`{name, category, price, minutes, spicy(0~3), main[], allergens[], tags[]}` 36개. 카테고리 6종, 알레르기 13종.
노트북에서 뽑을 때: `python3 -I .claude/skills/menu-domain-spec/scripts/extract_notebook.py <노트북.ipynb> data/`
데이터를 늘릴 때도 이 스키마와 알레르기 표기(`allergen_alias.json` 의 값 쪽)를 지킨다. 새 표기를 만들면 alias 에도 추가한다.

## 알려진 약점 (해결 대상)
| 노트북 10절 | 이번 해결 |
|---|---|
| relax 전에 묻지 않음 | interrupt 승인 |
| 이유 문장 미검증 (제육볶음 맵기2를 "매운맛 강함") | reviewer 노드가 이유 속 속성을 메뉴 데이터와 대조 |
| MemorySaver 휘발 | SQLite 영속 |
| 메뉴 하드코딩 | data/ + MCP Tool |
| 완화 정보 입력 제거 | 입력으로 복원 |

## 참고
- 노트북 원본 코드 전체(Before 구현): `references/notebook_baseline.md` — 이식하거나 Before 를 재현할 때 읽는다.
- 노트북 테스트 문장 3개(case1 해장 혼밥, case2 친구 매운 저녁+새우, case3 5000원+밀)는 회귀 테스트와 Before/After 에 그대로 쓴다. 문장 원문은 `menu-integration-qa` 스킬 3절에 있다.
