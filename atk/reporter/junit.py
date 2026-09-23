"""JUnit XML 输出：CI 平台通用测试报告格式。"""
import xml.etree.ElementTree as ET
from pathlib import Path

_BLOCKING = ("environment",)


def write_junit(report, out_path: Path | str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    suites = ET.Element("testsuites")
    suite = ET.SubElement(
        suites,
        "testsuite",
        {
            "name": f"atk-{report.env_name}",
            "tests": str(report.total),
            "failures": str(report.failed_count),
            "errors": "0",
            "skipped": str(report.blocked_count),
        },
    )
    for r in report.results:
        tc = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": r.scenario.module,
                "name": r.scenario.scenario,
                "time": f"{r.duration_ms / 1000:.3f}",
            },
        )
        if r.passed:
            continue
        detail = r.steps[-1].detail if r.steps else r.error_class
        if r.error_class in _BLOCKING:
            ET.SubElement(tc, "skipped", {"message": f"[{r.error_class}] {detail}"[:300]})
        else:
            f = ET.SubElement(
                tc,
                "failure",
                {"message": f"[{r.error_class}] {detail}"[:300], "type": r.error_class},
            )
            f.text = "\n".join(
            f"{s.title}: {s.detail}"
            + (f"\n实际返回: {s.response}" if s.response else "")
            for s in r.steps
        )
    ET.ElementTree(suites).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
