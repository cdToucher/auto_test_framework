from atk.executors.api_executor import StepResult
from atk.executors.runner import ScenarioResult
from atk.run_store import (
    IntentRecord,
    ScenarioSummary,
    add_evidence,
    create_run,
    load_run,
    save_run,
    summarize,
)
from atk.store.models import Scenario


def test_create_load_save_roundtrip(tmp_path):
    rec = create_run(
        base_ref="HEAD~1",
        head_ref="HEAD",
        affected_files=["a.py"],
        affected_modules=["demo"],
        planned_scenarios=["scenarios/demo/login.yaml"],
        runs_dir=tmp_path,
    )
    assert rec.run_id.startswith("smoke-")
    loaded = load_run(rec.run_id, runs_dir=tmp_path)
    assert loaded.base_ref == "HEAD~1"
    assert loaded.planned_scenarios == ["scenarios/demo/login.yaml"]
    loaded.intents.append(IntentRecord(title="登录页冒烟", status="pass"))
    save_run(loaded, runs_dir=tmp_path)
    again = load_run(rec.run_id, runs_dir=tmp_path)
    assert again.intents[0].title == "登录页冒烟"


def test_load_run_missing_raises(tmp_path):
    import pytest

    with pytest.raises(KeyError):
        load_run("smoke-nope", runs_dir=tmp_path)


def test_add_evidence_copies_files(tmp_path):
    src = tmp_path / "shot.png"
    src.write_bytes(b"\x89PNG fake")
    runs = tmp_path / "runs"
    rec = create_run(runs_dir=runs)
    saved = add_evidence(rec, [str(src), str(tmp_path / "ghost.png")], runs_dir=runs)
    assert saved == ["evidence/shot.png"]
    assert (runs / rec.run_id / "evidence" / "shot.png").exists()


def test_summarize_from_scenario_result():
    sc = Scenario(scenario="下单", module="order")
    sr = ScenarioResult(
        scenario=sc,
        passed=False,
        error_class="assertion",
        steps=[StepResult("POST /x", False, "status 期望 200 实际 500")],
        duration_ms=12,
        env="it",
    )
    s = summarize(sr)
    assert isinstance(s, ScenarioSummary)
    assert s.name == "下单" and s.passed is False and s.duration_ms == 12
    assert s.steps[0].detail.startswith("status")
