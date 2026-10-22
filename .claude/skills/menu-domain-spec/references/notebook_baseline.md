# 노트북 baseline 코드 (Before)

이전 과제 노트북 `AI_Agent_설계_및_구축_4반_G122_박영서.ipynb` 의 최종본 코드를 그대로 옮긴 것이다.
이식의 출발점이자 Before/After 비교의 Before 구현이다. 메뉴 36개 데이터는 `scripts/extract_notebook.py` 로 JSON 으로 뽑는다.

## 목차
- 5-3 조건 추출 (cell 10)
- 5-4 추천 스키마 (cell 12)
- 7-3 최종 추천 프롬프트 (cell 43)
- 5-2 알레르기 표기 (cell 8)
- 5-5 State (cell 14)
- 5-6 판정 함수 (cell 16)
- 5-6 노드 (cell 17)
- 5-7 라우팅 (cell 20)
- 5-8 그래프 조립 (cell 22)
- ask (cell 25)

## 5-3 조건 추출 (cell 10)

```python
class UserConstraints(BaseModel):
    budget: int | None = Field(None, description="1인 예산 상한, 원 단위. 언급이 없으면 null")
    max_minutes: int | None = Field(None, description="기다릴 수 있는 시간, 분 단위. 언급이 없으면 null")
    spicy_ok: bool | None = Field(None, description="매운 음식을 받아들일 수 있으면 true, 피하고 싶으면 false")
    avoid_keywords: list[str] = Field([], description="피하고 싶은 재료나 특징. 예: 기름진, 밀가루, 돼지고기")
    prefer_keywords: list[str] = Field([], description="원하는 특징. 예: 국물, 담백한, 든든한")
    allergies: list[str] = Field([], description="알레르기 재료. 확실히 알레르기라고 말한 것만")
    people: int = Field(1, description="함께 먹는 인원 수. 언급이 없으면 1")
    meal: str | None = Field(None, description="끼니. 아침, 점심, 저녁, 간식 중 하나. 언급이 없으면 null")


extract_prompt = ChatPromptTemplate.from_messages([
    ("system",
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
     "7. 아침, 점심, 저녁, 간식이라는 말이 나오면 meal 에 그대로 적는다"),
    ("human", "{user_input}"),
])

extractor = extract_prompt | model.with_structured_output(UserConstraints)
```

## 5-4 추천 스키마 (cell 12)

```python
class Pick(BaseModel):
    name: str = Field(description="후보 목록에 있는 메뉴 이름 그대로")
    reason: str = Field(description="이 상황에 맞는 이유. 두 문장 이내")


class Recommendation(BaseModel):
    picks: list[Pick] = Field(description="추천 메뉴 최대 3개")
    summary: str = Field(description="세 개를 관통하는 선택 기준 한 문장")


def format_candidates(menus):
    return "\n".join(
        f"- {m['name']} ({m['category']}) {m['price']}원 {m['minutes']}분 맵기{m['spicy']} "
        f"재료:{','.join(m['main'])} 특징:{','.join(m['tags'])}"
        for m in menus
    )
```

## 7-3 최종 추천 프롬프트 (cell 43)

```python
recommend_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "너는 상황에 맞는 메뉴를 골라 주는 도우미다.\n"
     "규칙:\n"
     "1. 반드시 아래 후보 목록 안에서만 고른다. 목록에 없는 메뉴는 절대 답하지 않는다\n"
     "2. 서로 다른 메뉴를 최대 3개 고른다\n"
     "3. 후보가 3개보다 적으면 있는 만큼만 고른다\n"
     "4. 조건이 서로 부딪히면 알레르기, 예산, 시간, 선호 순서로 지킨다\n"
     "5. 이유에는 그 메뉴의 속성 하나와 사용자 상황을 연결해 쓴다. 사용자 문장을 그대로 옮겨 적지 않는다\n"
     "6. 예산과 시간은 사용자 문장이 아니라 추출한 조건의 값을 기준으로 한다. 두 값이 다르면 조건 쪽이 맞다\n"
     "7. 이유와 summary 에 가격, 예산, 시간 숫자를 쓰지 않는다\n"),
    ("human",
     "사용자 문장: {user_input}\n\n"
     "추출한 조건: {constraints}\n\n"
     "후보 목록 ({n_candidates}개):\n{candidates}"),
])

recommender = recommend_prompt | model.with_structured_output(Recommendation)
```

## 5-2 알레르기 표기 (cell 8)

```python
ALLERGEN_ALIAS = {
    "밀가루": "밀", "면": "밀", "빵": "밀", "글루텐": "밀",
    "우유": "유제품", "치즈": "유제품", "크림": "유제품",
    "견과": "땅콩", "견과류": "땅콩", "땅콩류": "땅콩",
    "갑각류": "새우", "새우류": "새우",
    "조개류": "조개", "해산물": "새우",
    "달걀": "계란", "메밀면": "메밀",
    "돼지": "돼지고기", "소": "소고기", "닭": "닭고기",
    "콩": "대두", "두부": "대두",
}


def normalize_allergens(items):
    return [ALLERGEN_ALIAS.get(a.strip(), a.strip()) for a in items]
```

## 5-5 State (cell 14)

```python
class MenuState(TypedDict):
    user_input: str
    constraints: dict
    candidates: list
    relax_count: int
    relaxed: list
    recommendation: dict
    attempts: int
    kept: list
    dropped: list
    summary: str
    recent: list
```

## 5-6 판정 함수 (cell 16)

```python
def block_reason(m, c, recent):
    if any(a in m["allergens"] for a in c.allergies):
        return "알레르기 재료 포함"
    if c.spicy_ok is False and m["spicy"] >= 2:
        return "매운 메뉴"
    if c.meal in ("점심", "저녁") and "간식" in m["tags"]:
        return "끼니가 아닌 간식"
    if m["name"] in recent:
        return "직전에 추천한 메뉴"
    return None


def over_limit(m, c):
    if c.budget is not None and m["price"] > c.budget:
        return f"예산 초과 {m['price']}원"
    if c.max_minutes is not None and m["minutes"] > c.max_minutes:
        return f"시간 초과 {m['minutes']}분"
    return None


def match_score(m, c):
    words = m["tags"] + m["main"] + [m["category"]]
    score = sum(k in words for k in c.prefer_keywords) - sum(k in words for k in c.avoid_keywords)
    if c.spicy_ok is True and m["spicy"] >= 2:
        score += 1
    return score
```

## 5-6 노드 (cell 17)

```python
def extract(state):
    c = extractor.invoke({"user_input": state["user_input"]})
    c = c.model_copy(update={"allergies": normalize_allergens(c.allergies)})
    print(f"extract: 예산 {c.budget}, 시간 {c.max_minutes}, 끼니 {c.meal}, 알레르기 {c.allergies}")
    return {"constraints": c.model_dump()}


def filter_menus(state):
    c = UserConstraints(**state["constraints"])
    recent = state.get("recent", [])
    passed = [m for m in MENUS if block_reason(m, c, recent) is None and over_limit(m, c) is None]
    passed.sort(key=lambda m: match_score(m, c), reverse=True)
    print(f"filter: 후보 {len(passed[:10])}개")
    return {"candidates": passed[:10]}


def relax(state):
    c = UserConstraints(**state["constraints"])
    safe = [m for m in MENUS if block_reason(m, c, state.get("recent", [])) is None]
    relaxed = list(state["relaxed"])
    if c.budget is not None and state["relax_count"] == 0:
        prices = sorted(m["price"] for m in safe if c.max_minutes is None or m["minutes"] <= c.max_minutes)
        if prices:
            new = prices[min(2, len(prices) - 1)]
            relaxed.append(f"예산 {c.budget}원 → {new}원")
            c = c.model_copy(update={"budget": new})
    elif c.max_minutes is not None:
        minutes = sorted(m["minutes"] for m in safe if c.budget is None or m["price"] <= c.budget)
        if minutes:
            new = minutes[min(2, len(minutes) - 1)]
            relaxed.append(f"시간 {c.max_minutes}분 → {new}분")
            c = c.model_copy(update={"max_minutes": new})
    print(f"relax: {relaxed[-1] if relaxed else '풀 수 있는 조건 없음'}")
    return {"constraints": c.model_dump(), "relaxed": relaxed, "relax_count": state["relax_count"] + 1}


def recommend(state):
    banned = [name for name, _ in state["dropped"]]
    cands = [m for m in state["candidates"] if m["name"] not in banned]
    rec = recommender.invoke({
        "user_input": state["user_input"],
        "constraints": UserConstraints(**state["constraints"]).model_dump_json(),
        "relaxed": ", ".join(state["relaxed"]) or "없음",
        "candidates": format_candidates(cands),
        "n_candidates": len(cands),
    })
    print(f"recommend: {state['attempts'] + 1}번째, 후보 {len(cands)}개에서 {[p.name for p in rec.picks]}")
    return {"recommendation": rec.model_dump(), "attempts": state["attempts"] + 1}


def verify(state):
    c = UserConstraints(**state["constraints"])
    recent = state.get("recent", [])
    by_name = {m["name"]: m for m in MENUS}
    kept, dropped = [], list(state["dropped"])
    rec = state["recommendation"]
    for p in rec["picks"]:
        m = by_name.get(p["name"])
        why = "메뉴 데이터에 없는 이름" if m is None else block_reason(m, c, recent) or over_limit(m, c)
        if why:
            dropped.append((p["name"], why))
        else:
            kept.append({"name": m["name"], "price": m["price"], "minutes": m["minutes"], "reason": p["reason"]})
    print(f"verify: 통과 {len(kept)}개, 걸러짐 {len(dropped) - len(state['dropped'])}개")
    return {"kept": kept, "dropped": dropped, "summary": rec["summary"]}


def remember(state):
    recent = (state.get("recent", []) + [k["name"] for k in state["kept"]])[-6:]
    print(f"remember: 최근 추천 {recent}")
    return {"recent": recent}


def no_result(state):
    print("no_result")
    return {"kept": [], "summary": "조건에 맞는 메뉴를 찾지 못했습니다. 알레르기, 맵기, 끼니 조건은 풀지 않았습니다."}
```

## 5-7 라우팅 (cell 20)

```python
def route_after_filter(state):
    if state["candidates"]:
        return "recommend"
    if state["relax_count"] < 2:
        return "relax"
    return "no_result"


def route_after_verify(state):
    if state["dropped"] and state["attempts"] < 2:
        return "recommend"
    return "remember"
```

## 5-8 그래프 조립 (cell 22)

```python
builder = StateGraph(MenuState)

builder.add_node("extract", extract)
builder.add_node("filter", filter_menus)
builder.add_node("relax", relax)
builder.add_node("recommend", recommend)
builder.add_node("verify", verify)
builder.add_node("remember", remember)
builder.add_node("no_result", no_result)

builder.add_edge(START, "extract")
builder.add_edge("extract", "filter")
builder.add_conditional_edges(
    "filter",
    route_after_filter,
    {"recommend": "recommend", "relax": "relax", "no_result": "no_result"},
)
builder.add_edge("relax", "filter")
builder.add_edge("recommend", "verify")
builder.add_conditional_edges(
    "verify",
    route_after_verify,
    {"recommend": "recommend", "remember": "remember"},
)
builder.add_edge("remember", END)
builder.add_edge("no_result", END)

graph = builder.compile(checkpointer=MemorySaver())
```

## ask (cell 25)

```python
def ask(user_input, thread_id):
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke({
        "user_input": user_input,
        "relax_count": 0,
        "relaxed": [],
        "attempts": 0,
        "dropped": [],
        "kept": [],
    }, config=config)


def show(r):
    print()
    print("조건 완화:", ", ".join(r["relaxed"]) or "없음")
    print("후보:", ", ".join(m["name"] for m in r["candidates"]) or "없음")
    print("추천")
    for k in r["kept"]:
        print(f"  {k['name']} ({k['price']}원, {k['minutes']}분) - {k['reason']}")
    print("걸러진 것:", r["dropped"] or "없음")
    print("요약:", r["summary"])
```
