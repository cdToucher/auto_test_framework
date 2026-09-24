import xml.etree.ElementTree as ET

import pytest

from atk.executors.api_executor import StepResult
from atk.executors.runner import RunReport, ScenarioResult
from atk.policy import JUNIT_NODE, TIER
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
    # 受阻进 <error>：JUnit 的 errors 就是指"执行期出错、不是断言不成立"，
    # 旧实现把它硬编码成 0 并把 blocked_count 填进 skipped，两个出口口径不一致。
    assert suite.get("errors") == "1"
    assert suite.get("skipped") == "0"
    cases = suite.findall("testcase")
    assert len(cases) == 3
    assert cases[0].find("failure") is None and cases[0].find("skipped") is None
    f = cases[1].find("failure")
    assert f is not None and "200" in (f.get("message") or "")
    assert cases[2].find("error") is not None


def _one(name, passed, cls):
    rep = RunReport(env_name="it", started_at="2026-08-25T10:00:00")
    rep.results.append(ScenarioResult(
        scenario=Scenario(scenario=name, module="m"),
        passed=passed, error_class=cls,
        steps=[StepResult("step", passed, "现场")], duration_ms=1, env="it",
    ))
    return rep


@pytest.mark.parametrize("error_class", sorted(TIER))
def test_every_error_class_has_a_junit_node(tmp_path, error_class):
    """每个归类都必须落到一个明确的 junit 节点上，不许有第三种答案。"""
    out = tmp_path / "junit.xml"
    write_junit(_one("s", error_class == "none", error_class), out)
    tc = ET.parse(out).getroot().find("testsuite").find("testcase")
    want = JUNIT_NODE[error_class]
    if want == "ok":
        assert len(list(tc)) == 0
    else:
        assert len(list(tc)) == 1 and tc.find(want) is not None


def test_suite_attributes_match_case_nodes(tmp_path):
    """套件级计数必须等于逐用例节点数：这是旧实现最容易悄悄漂移的地方。"""
    rep = RunReport(env_name="it", started_at="2026-08-25T10:00:00")
    rep.results = [
        ScenarioResult(scenario=Scenario(scenario=f"s{i}", module="m"), passed=p,
                       error_class=c, steps=[StepResult("t", p, "d")], env="it")
        for i, (p, c) in enumerate([
            (True, "none"), (False, "assertion"), (False, "config"),
            (False, "environment"), (False, "ui_pending"), (False, "skipped"),
        ])
    ]
    out = tmp_path / "junit.xml"
    write_junit(rep, out)
    suite = ET.parse(out).getroot().find("testsuite")
    nodes = [child for tc in suite.findall("testcase") for child in tc]
    assert int(suite.get("tests")) == len(suite.findall("testcase"))
    assert int(suite.get("failures")) == sum(1 for n in nodes if n.tag == "failure")
    assert int(suite.get("errors")) == sum(1 for n in nodes if n.tag == "error")
    assert int(suite.get("skipped")) == sum(1 for n in nodes if n.tag == "skipped")
    # 每个非通过用例恰好一个节点：不许有用例既不算通过也没有任何交代
    assert len(nodes) == sum(1 for r in rep.results if not r.passed)
    assert len(nodes) == (int(suite.get("failures")) + int(suite.get("errors"))
                          + int(suite.get("skipped")))


def test_ui_pending_alone_does_not_report_failures(tmp_path):
    """runner 对 ui_pending 判 exit 0，junit 就不许报 failures——两边必须同源。"""
    out = tmp_path / "junit.xml"
    write_junit(_one("待实测", False, "ui_pending"), out)
    suite = ET.parse(out).getroot().find("testsuite")
    assert suite.get("failures") == "0" and suite.get("errors") == "0"
    assert suite.get("skipped") == "1"
    assert _one("待实测", False, "ui_pending").exit_code == 0


def test_unknown_error_class_never_becomes_failure(tmp_path):
    """未登记的归类是 gate/report 的兜底面：宁可显示未测，不许显示改动坏了。"""
    out = tmp_path / "junit.xml"
    write_junit(_one("怪", False, "who_knows"), out)
    tc = ET.parse(out).getroot().find("testsuite").find("testcase")
    assert tc.find("failure") is None and tc.find("skipped") is not None
    assert tc.find("skipped").get("type") == "who_knows"
