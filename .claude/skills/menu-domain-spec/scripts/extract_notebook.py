"""이전 과제 노트북에서 메뉴 데이터와 알레르기 표기 사전을 뽑아 JSON으로 저장한다.

노트북 코드를 실행하지 않고 ast 로 리터럴만 읽는다. LLM 호출이나 .env 로딩이 섞여 있어서다.

사용법:
    python3 -I extract_notebook.py <notebook.ipynb> <out_dir> [--raw]
    -> <out_dir>/menus.json, <out_dir>/allergen_alias.json

기본값은 노트북 값에 AFTER_FIXES 를 덮어쓴 After 데이터다.
--raw 는 노트북 그대로(Before 재현용)다.
"""
import ast
import json
import sys
from pathlib import Path


# 노트북 사전의 버그 수정. 상위 개념은 하위 재료 전부로 펼친다.
# 노트북은 "해산물": "새우" 하나만 매핑해서 조개·오징어·생선 메뉴가 통과했다(QA 회차 2).
AFTER_FIXES = {
    "해산물": ["새우", "오징어", "조개", "생선"],
}


def find_literal(nb, name):
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        src = "".join(cell["source"])
        if f"{name} =" not in src:
            continue
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return ast.literal_eval(node.value)
    raise SystemExit(f"{name} 리터럴을 노트북에서 찾지 못했다")


def main():
    args = [a for a in sys.argv[1:] if a != "--raw"]
    raw = "--raw" in sys.argv[1:]
    if len(args) != 2:
        raise SystemExit(__doc__)
    nb = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    out = Path(args[1])
    out.mkdir(parents=True, exist_ok=True)

    menus = find_literal(nb, "MENUS")
    alias = find_literal(nb, "ALLERGEN_ALIAS")
    if not raw:
        alias.update(AFTER_FIXES)

    required = {"name", "category", "price", "minutes", "spicy", "main", "allergens", "tags"}
    for m in menus:
        missing = required - m.keys()
        if missing:
            raise SystemExit(f"{m.get('name')} 에 필드 누락: {missing}")
    names = [m["name"] for m in menus]
    if len(names) != len(set(names)):
        raise SystemExit("메뉴 이름 중복")

    known = {a for m in menus for a in m["allergens"]}
    for k, v in alias.items():
        for a in (v if isinstance(v, list) else [v]):
            if a not in known:
                raise SystemExit(f"alias {k} -> {a} 는 메뉴 데이터에 없는 알레르기 표기")
    (out / "menus.json").write_text(json.dumps(menus, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "allergen_alias.json").write_text(json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8")
    mode = "raw(Before)" if raw else "After"
    print(f"[{mode}] menus.json {len(menus)}개, allergen_alias.json {len(alias)}개 -> {out}")


if __name__ == "__main__":
    main()
