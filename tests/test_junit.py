import xml.etree.ElementTree as ET

from atk.executors.api_executor import StepResult
from atk.executors.runner import RunReport, ScenarioResult
from atk.reporter.junit import write_junit
from atk.store.models import Scenario


def _report():
    rep = RunReport(env_name="it", started_at="2026-08-25T10:00:00")

    def mk(name, passed, cls, detail=""):
        return ScenarioResult(
            scenario=Scenario(scenario=name, module="m"),
            passed=passed,
            error_class=cls,
            steps=[StepResult("step", passed, detail)],
            duration_ms=5,
            env="it",
        )

    rep.results.append(mk("通过场景", True, "none"))
    rep.results.append(mk("断言失败", False, "assertion", "status 期望 200 实际 500"))
    rep.results.append(mk("环境受阻", False, "environment", "connect refused"))
    return rep


def test_junit_structure(tmp_path):
    out = tmp_path / "junit.xml"
    write_junit(_report(), out)
    root = ET.parse(out).getroot()
    suite = root.find("testsuite")
    assert suite.get("tests") == "3"
    assert suite.get("failures") == "1"
    assert suite.get("skipped") == "1"
    cases = suite.findall("testcase")
    assert len(cases) == 3
    assert cases[0].find("failure") is None and cases[0].find("skipped") is None
    f = cases[1].find("failure")
    assert f is not None and "200" in (f.get("message") or "")
    assert cases[2].find("skipped") is not None
