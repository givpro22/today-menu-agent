"""구조화 출력 스키마 — 노트북 5-3(UserConstraints), 5-4(Pick/Recommendation) 이식 + reviewer.

필드 description 이 JSON Schema 로 모델에 넘어간다(ollama `format=`, openai 시스템 프롬프트).
단위와 "언급 없으면 null" 은 여기 적고 프롬프트에 같은 말을 반복하지 않는다.
State 에는 이 객체를 넣지 않고 `model_dump()` dict 만 둔다(노트북 11절 — 체크포인트 역직렬화).
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class RouteResult(BaseModel):
    route: Literal["menu", "chat"] = Field(description="메뉴 추천 요청이면 menu, 그 밖의 대화면 chat")


class UserConstraints(BaseModel):
    budget: int | None = Field(None, description="1인 예산 상한, 원 단위. 언급이 없으면 null")
    max_minutes: int | None = Field(None, description="기다릴 수 있는 시간, 분 단위. 언급이 없으면 null")
    spicy_ok: bool | None = Field(None, description="매운 음식을 받아들일 수 있으면 true, 피하고 싶으면 false")
    avoid_keywords: list[str] = Field([], description="피하고 싶은 재료나 특징. 예: 기름진, 밀가루, 돼지고기")
    prefer_keywords: list[str] = Field([], description="원하는 특징. 예: 국물, 담백한, 든든한")
    allergies: list[str] = Field([], description="알레르기 재료. 확실히 알레르기라고 말한 것만")
    people: int = Field(1, description="함께 먹는 인원 수. 언급이 없으면 1")
    meal: str | None = Field(None, description="끼니. 아침, 점심, 저녁, 간식 중 하나. 언급이 없으면 null")


class Pick(BaseModel):
    name: str = Field(description="후보 목록에 있는 메뉴 이름 그대로")
    reason: str = Field(description="이 상황에 맞는 이유. 두 문장 이내")


class Recommendation(BaseModel):
    picks: list[Pick] = Field(description="추천 메뉴 최대 3개")
    summary: str = Field(description="세 개를 관통하는 선택 기준 한 문장")


class ReviewVerdict(BaseModel):
    name: str = Field(description="검사한 메뉴 이름 그대로")
    contradiction: bool = Field(description="이유 문장이 메뉴 데이터와 모순되면 true")
    evidence: str = Field("", description="모순 근거 한 문장. 모순이 없으면 빈 문자열")


class ReviewResult(BaseModel):
    verdicts: list[ReviewVerdict] = Field(description="입력 메뉴마다 판정 하나")


def parse_as(content: str, model_cls: type[BaseModel]) -> dict | None:
    """LLM 텍스트 → 스키마 검증된 dict. 실패하면 None(호출자가 재시도·폴백).
    코드 펜스·앞뒤 잡문을 걷어 내고, list 필드에 온 null 은 기본값으로 돌린다."""
    match = re.search(r"\{.*\}", content or "", re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    fields = model_cls.model_fields
    raw = {k: v for k, v in raw.items()
           if k in fields and not (v is None and fields[k].default is not None)}
    try:
        return model_cls(**raw).model_dump()
    except (ValidationError, TypeError):
        return None
