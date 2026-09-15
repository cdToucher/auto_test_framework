"""P2 清尾：草稿转正保注释、fixture 内 ${env:VAR} 替换。"""
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


# ------------------------------------------------------------------ 草稿转正保注释

def test_approve_preserves_comments_inline(tmp_path):
    p = _mk(tmp_path, DRAFT_INLINE)
    review_draft(p, "dev", "approve", reports_dir=tmp_path / "reports")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# 这是注释，转正时必须保留")
    assert "# 行尾注释" in text
    data = yaml.safe_load(text)
    assert data["tags"] == ["smoke"]
    # 未被 safe_dump 重排：steps 仍是原来的块结构文本
    assert 'call: "POST /api/orders"' in text


def test_approve_preserves_comments_block(tmp_path):
    p = _mk(tmp_path, DRAFT_BLOCK)
    review_draft(p, "dev", "approve", reports_dir=tmp_path / "reports")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# 块列表写法")
    assert yaml.safe_load(text)["tags"] == ["smoke"]


def test_approve_removes_tags_key_when_empty(tmp_path):
    p = _mk(tmp_path, DRAFT_ONLY_TAG)
    review_draft(p, "dev", "approve", reports_dir=tmp_path / "reports")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "tags" not in data


def test_approve_result_is_valid_yaml_and_loses_draft(tmp_path):
    for content in (DRAFT_INLINE, DRAFT_BLOCK, DRAFT_ONLY_TAG):
        p = _mk(tmp_path, content, name=f"d{abs(hash(content))}.yaml")
        review_draft(p, "dev", "approve", reports_dir=tmp_path / "reports")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert "ai-generated" not in (data.get("tags") or [])
        assert data["steps"]


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
