"""P2 清尾：草稿评审不动场景文件、fixture 内 ${env:VAR} 替换。"""
import os

import pytest
import yaml

from atk.drafts import review_draft
from atk.executors.runner import Runner

DRAFT_INLINE = """\
# 这是注释，转正时必须保留
scenario: 优惠券下单
module: order
priority: P0
tags: [ai-generated, smoke]   # 行尾注释
steps:
  - api:
      call: "POST /api/orders"
      expect: { status: 200 }
"""

DRAFT_BLOCK = """\
# 块列表写法
scenario: 下单
module: order
tags:
  - ai-generated
  - smoke
steps:
  - api:
      call: "GET /ping"
      expect: { status: 200 }
"""

DRAFT_ONLY_TAG = """\
scenario: 只有草稿标签
module: order
tags: [ai-generated]
steps:
  - api:
      call: "GET /ping"
      expect: { status: 200 }
"""


def _mk(tmp_path, content, name="gen-draft.yaml"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ------------------------------------------------------------------ 草稿评审不再改文件

def test_approve_leaves_every_byte_untouched(tmp_path):
    """approve 不再做任何文本手术：注释、行尾注释、块结构、tags 全部原样。

    曾经的"去 tag 保注释"逐行手术（两种 tags 写法 + YAML 重写兜底）连同它的
    全部用例一起删除——转正判据是审批账本里的内容签名，文件本来就不该被改。
    """
    for content in (DRAFT_INLINE, DRAFT_BLOCK, DRAFT_ONLY_TAG):
        p = _mk(tmp_path, content, name=f"d{abs(hash(content))}.yaml")
        review_draft(p, "dev", "approve", reports_dir=tmp_path / "reports")
        assert p.read_text(encoding="utf-8") == content
        assert yaml.safe_load(content)["steps"], "场景本身仍是合法 YAML"


# ------------------------------------------------------------------ fixture 变量替换

def test_fixture_supports_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("ATK_SECRET", "s3cret")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "s.yaml").write_text(
        "scenario: s\nmodule: m\ndata: fx.yaml\nsteps:\n"
        "  - api:\n      call: \"GET /ping\"\n      expect: { status: 200 }\n",
        encoding="utf-8",
    )
    (tmp_path / "fx.yaml").write_text("token: \"${env:ATK_SECRET}\"\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        "local:\n  base_url: 'http://127.0.0.1:1'\n  vars: {}\n", encoding="utf-8"
    )

    captured = {}

    class _Exec:
        def __init__(self, client, variables):
            captured.update(variables)

        def execute(self, step):
            from atk.executors.api_executor import StepResult

            return StepResult(step.call, True, "200")

    monkeypatch.setattr("atk.executors.runner.ApiExecutor", _Exec)
    rep = Runner(
        env_file=tmp_path / "config" / "environments.yaml",
        scenarios_root=tmp_path / "scenarios",
    ).run(env_name="local")
    assert rep.total == 1
    assert captured.get("token") == "s3cret"


def test_fixture_non_mapping_rejected(tmp_path):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "s.yaml").write_text(
        "scenario: s\nmodule: m\ndata: fx.yaml\nsteps:\n"
        "  - api:\n      call: \"GET /ping\"\n      expect: { status: 200 }\n",
        encoding="utf-8",
    )
    (tmp_path / "fx.yaml").write_text("- a\n- b\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        "local:\n  base_url: 'http://127.0.0.1:1'\n  vars: {}\n", encoding="utf-8"
    )
    rep = Runner(
        env_file=tmp_path / "config" / "environments.yaml",
        scenarios_root=tmp_path / "scenarios",
    ).run(env_name="local")
    assert rep.failed_count == 1
    assert "fixture 顶层必须是映射" in rep.results[0].steps[0].detail
