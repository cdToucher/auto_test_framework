"""skill 正文里对代码的断言，必须能被代码本身验证。

这类文档最容易写歪：命令改名、说明书搬家、expect 语法记错，正文都不会自己跟着变。
所以这里不比对文案，直接把正文里的 YAML 示例喂给真加载器、把表达式喂给真解析器。
"""
import re
from pathlib import Path

import pytest

from atk.skill_install import USE_SKILL, package_skill_texts, use_file
from atk.store.loader import load_scenarios
from atk.store.models import parse_expect_map, parse_expect_value

SKILLS = package_skill_texts()


def _section(text: str, heading: str) -> str:
    body = text.split(heading, 1)
    assert len(body) == 2, f"正文里找不到小节：{heading}"
    return body[1].split("\n## ", 1)[0]


def test_skills_do_not_point_at_retired_manual_path():
    """说明书搬过一次家；再搬必须让引用跟着走，否则 AI 会照着读不存在的文件。"""
    want = use_file().as_posix()          # .atk/skills/atk-use/atk_use.md
    for name, text in SKILLS.items():
        if name == USE_SKILL:
            continue
        assert ".atk/atk_use.md" not in text, f"{name} 还指着旧落位"
        assert want in text, f"{name} 没引用当前说明书路径 {want}"


def test_authoring_yaml_examples_load_with_real_loader(tmp_path):
    """示例里的 YAML 必须真能加载：字段名、tags、expect 写法错一个就该被发现。"""
    lib = tmp_path / "scenarios"
    lib.mkdir()
    blocks = re.findall(r"```yaml\n(.*?)```", SKILLS["atk-authoring"], re.S)
    assert blocks, "起草协议里没有 YAML 示例"
    for i, block in enumerate(blocks):
        (lib / f"x{i}.yaml").write_text(block, encoding="utf-8")

    scs, errors = load_scenarios(lib)
    assert errors == []
    assert len(scs) == len(blocks)
    api = next(s for s in scs if all(x.type == "api" for x in s.steps))
    mixed = next(s for s in scs if any(x.type == "ui" for x in s.steps))
    assert len(api.steps) == 3 and api.steps[0].expect, "API 示例步骤/断言被改坏"
    assert mixed.steps[0].type == "api", "E2E 示例应先备数据再进 UI"
    assert [s.type for s in mixed.steps][1:] == ["ui"] * (len(mixed.steps) - 1), \
        "ui: 步骤后面不该再串 api（atk run 到第一个 ui 就中断）"
    assert all("ai-generated" in s.tags for s in scs), "示例必须示范草稿标记"


def test_expect_table_expressions_parse_as_documented():
    """expect 表里每个带引号表达式都得真被解析成操作符，写歪即失败。"""
    table = _section(SKILLS["atk-authoring"], "### expect 能写什么")
    # 只看语法表的表格行（正文里的中文引号不算示例），抽引号内表达式
    rows = "\n".join(l for l in table.splitlines() if l.startswith("|"))
    exprs = {e for e in re.findall(r'"([^"]+)"', rows) if not e.startswith("\\")}
    assert len(exprs) >= 6, f"表里可校验的表达式太少：{sorted(exprs)}"
    for expr in exprs:
        op, _ = parse_expect_value(expr)
        assert op != "eq", f"文档把 {expr!r} 当操作符，解析器只认它是字面量"


@pytest.mark.parametrize(
    "expr, want_op, want_val",
    [
        ("in: pending, paid", "in", ["pending", "paid"]),   # 逗号分隔，空格不算分隔
        ("len: 1..5", "len", {"min": 1, "max": 5}),          # 闭区间
        ("type: str", "type", "str"),
        ("startswith: ORD", "startswith", "ORD"),
        ("\\<deprecated", "eq", "<deprecated"),              # 反斜杠转义成字面量
    ],
)
def test_documented_expression_semantics(expr, want_op, want_val):
    assert parse_expect_value(expr) == (want_op, want_val)


def test_status_rejects_nullary_ops_as_documented():
    """文档写明 status 不能断 not_null，代码得真的拒。"""
    with pytest.raises(ValueError):
        parse_expect_map({"status": "not_null"})
    # 而字段路径可以用 not_null
    rules = parse_expect_map({"data.orderNo": "not_null"})
    assert rules[0].op == "not_null"
