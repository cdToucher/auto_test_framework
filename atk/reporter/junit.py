"""JUnit XML 输出：CI 平台通用测试报告格式。

节点类型与套件计数都取自 policy，这里不再自己判断"什么算失败"——
JUnit 侧的结论必须和 `atk run` 的退出码同源，否则 CI 与本地会各说一套。
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from ..policy import junit_counts, junit_node, tally

_DETAIL_LIMIT = 300


def write_junit(report, out_path: Path | str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = tally(report.results)
    suites = ET.Element("testsuites")
    suite = ET.SubElement(
        suites,
        "testsuite",
        {"name": f"atk-{report.env_name}", **junit_counts(t)},
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
        node = junit_node(r.error_class if not r.passed else "none")
        if node == "ok":
            continue
        detail = r.steps[-1].detail if r.steps else r.error_class
        message = f"[{r.error_class}] {detail}"[:_DETAIL_LIMIT]
        el = ET.SubElement(tc, node, {"message": message, "type": r.error_class})
        if node != "skipped":
            # 待实测/跳过没有"现场"可给：把未执行的步骤印成失败现场只会让人以为跑过了。
            el.text = "\n".join(
                f"{s.title}: {s.detail}"
                + (f"\n实际返回: {s.response}" if s.response else "")
                for s in r.steps
            )
    ET.ElementTree(suites).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
