"""이전 과제 노트북에서 메뉴 데이터와 알레르기 표기 사전을 뽑아 JSON으로 저장한다.

노트북 코드를 실행하지 않고 ast 로 리터럴만 읽는다. LLM 호출이나 .env 로딩이 섞여 있어서다.

사용법:
    python3 -I extract_notebook.py <notebook.ipynb> <out_dir>
    -> <out_dir>/menus.json, <out_dir>/allergen_alias.json
"""
import ast
import json
import sys
from pathlib import Path


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
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    nb = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)

    menus = find_literal(nb, "MENUS")
    alias = find_literal(nb, "ALLERGEN_ALIAS")

    required = {"name", "category", "price", "minutes", "spicy", "main", "allergens", "tags"}
    for m in menus:
        missing = required - m.keys()
        if missing:
            raise SystemExit(f"{m.get('name')} 에 필드 누락: {missing}")
    names = [m["name"] for m in menus]
    if len(names) != len(set(names)):
        raise SystemExit("메뉴 이름 중복")

    (out / "menus.json").write_text(json.dumps(menus, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "allergen_alias.json").write_text(json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"menus.json {len(menus)}개, allergen_alias.json {len(alias)}개 -> {out}")


if __name__ == "__main__":
    main()
