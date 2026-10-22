"""프롬프트 — openai_llm.py / ollama_llm.py 공용.

extract 는 노트북 5-3, recommend 는 노트북 7-3 최종본을 그대로 옮겼다. 바뀐 것은 recommend
입력에 "조건 완화" 칸을 되살린 것 하나다(노트북 10절: 완화 정보를 입력에서 뺐더니 모델이
사용자 문장의 5000원을 믿고 완화로 들어온 된장찌개 8500원을 뺐다). 규칙 7(숫자 금지)은 유지.
"""
import json

from pydantic import BaseModel

# ---------- route (sample 라우터, 라벨 incident → menu)
ROUTE_SYSTEM = (
    "너는 메뉴 추천 서비스의 라우터다. 사용자 메시지를 분류하라. "
    "식사 상황, 먹고 싶은 것, 예산, 알레르기, 끼니, 메뉴 추천 요청이면 route=menu, "
    "인사·잡담·이름 소개·서비스 질문 등 그 밖의 메시지면 route=chat."
)

# ---------- extract (노트북 5-3)
EXTRACT_SYSTEM = (
    "너는 사용자의 식사 상황 문장에서 메뉴 선택 조건만 뽑아내는 추출기다.\n"
    "규칙:\n"
    "1. 문장에 없는 조건은 추측하지 말고 비워 둔다\n"
    "2. 어제 먹은 음식이 언급되면 그 음식과 같은 재료를 avoid_keywords 에 넣는다\n"
    "3. 속이 안 좋다, 숙취 같은 표현이 나오면 prefer_keywords 에 속편한 을 넣는다\n"
    "4. 알레르기는 사용자가 알레르기라고 분명히 말한 경우에만 allergies 에 넣는다\n"
    "5. budget 은 반드시 1인 기준 금액이다. 총액을 말하면 인원수로 나눈다\n"
    "   예: 네 명이서 6만원 이면 budget 은 15000 이다\n"
    "   예: 1인 15000원 이면 budget 은 15000 이다. 인원수를 곱하지 않는다\n"
    "6. 알레르기는 재료 이름으로 적는다. 면 이나 빵 대신 밀 처럼 재료로 바꾼다\n"
    "7. 아침, 점심, 저녁, 간식이라는 말이 나오면 meal 에 그대로 적는다"
)

# ---------- recommend (노트북 7-3 최종본)
RECOMMEND_SYSTEM = (
    "너는 상황에 맞는 메뉴를 골라 주는 도우미다.\n"
    "규칙:\n"
    "1. 반드시 아래 후보 목록 안에서만 고른다. 목록에 없는 메뉴는 절대 답하지 않는다\n"
    "2. 서로 다른 메뉴를 최대 3개 고른다\n"
    "3. 후보가 3개보다 적으면 있는 만큼만 고른다\n"
    "4. 조건이 서로 부딪히면 알레르기, 예산, 시간, 선호 순서로 지킨다\n"
    "5. 이유에는 그 메뉴의 속성 하나와 사용자 상황을 연결해 쓴다. 사용자 문장을 그대로 옮겨 적지 않는다\n"
    "6. 예산과 시간은 사용자 문장이 아니라 추출한 조건의 값을 기준으로 한다. 두 값이 다르면 조건 쪽이 맞다\n"
    "7. 이유와 summary 에 가격, 예산, 시간 숫자를 쓰지 않는다\n"
)
RECOMMEND_HUMAN = (
    "사용자 문장: {query}\n\n"
    "추출한 조건: {constraints}\n\n"
    "조건 완화(사용자가 승인함, 추출한 조건에 이미 반영됨): {relaxed}\n\n"
    "후보 목록 ({n_candidates}개):\n{candidates}"
)

# ---------- review (이번 프로젝트 신규 — LLM-as-judge)
REVIEW_SYSTEM = (
    "너는 메뉴 추천 이유를 검사하는 심사자다.\n"
    "각 메뉴의 이유 문장이 그 메뉴 데이터와 모순되는지만 판정한다.\n"
    "규칙:\n"
    "1. 맵기는 0~3 이다. 0~1 은 맵지 않은 메뉴다\n"
    "2. 데이터에 없는 재료나 특징을 사실처럼 말하면 모순이다\n"
    "3. 데이터와 어긋나지 않는 일반적인 표현(든든하다, 잘 맞는다)은 모순이 아니다\n"
    "4. 모순이면 contradiction=true 와 근거 한 문장, 아니면 contradiction=false 와 빈 근거\n"
    "5. 입력 메뉴마다 판정을 하나씩, 이름은 입력 그대로 적는다"
)

# ---------- chat (sample chat_answer, 서비스 소개만 바꿈)
CHAT_SYSTEM = (
    "당신은 '오늘뭐먹지' 메뉴 추천 도우미다. 간결한 한국어로 한두 문장으로 답하라. "
    "식사 상황을 한 문장으로 적어 주면 조건에 맞는 메뉴를 3개까지 추천해 줄 수 있다고 안내하라. "
    "대화 이력에서 사용자가 밝힌 이름이 있으면 정확히 기억해 답하라."
)


def json_instructions(model_cls: type[BaseModel]) -> str:
    """openai JSON 모드용 — 스키마를 강제하지 못하므로 시스템 프롬프트에 붙인다."""
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
    return f"JSON 객체로만 응답하라. 스키마(JSON Schema): {schema}"


def format_candidates(menus: list[dict]) -> str:
    """노트북 5-4 그대로."""
    return "\n".join(
        f"- {m['name']} ({m['category']}) {m['price']}원 {m['minutes']}분 맵기{m['spicy']} "
        f"재료:{','.join(m['main'])} 특징:{','.join(m['tags'])}"
        for m in menus
    )


def format_relaxed(relaxed: list[dict]) -> str:
    return ", ".join(f"{r['label']} {r['from']}{r['unit']} → {r['to']}{r['unit']}"
                     for r in relaxed) or "없음"


def recommend_user(query: str, constraints: dict, relaxed: list[dict],
                   candidates: list[dict]) -> str:
    return RECOMMEND_HUMAN.format(
        query=query,
        constraints=json.dumps(constraints, ensure_ascii=False),
        relaxed=format_relaxed(relaxed),
        n_candidates=len(candidates),
        candidates=format_candidates(candidates),
    )


def review_user(items: list[dict]) -> str:
    lines = []
    for it in items:
        m = it["menu"]
        lines.append(
            f"- {it['name']}: 맵기{m['spicy']} 재료:{','.join(m['main'])} "
            f"특징:{','.join(m['tags'])} 알레르기:{','.join(m['allergens'])}\n"
            f"  이유: {it['reason']}")
    return "검사할 메뉴:\n" + "\n".join(lines)


def chat_user(query: str, history: list[dict]) -> str:
    rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
    return f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n## 사용자의 새 메시지\n{query}"
