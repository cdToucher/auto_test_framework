"""HTML 报告渲染：零模板引擎，f-string 生成单文件。"""
import html as _html
from pathlib import Path

from ..executors.runner import RunReport

_CSS = """
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:24px;color:#1a1a1a}
h1{font-size:20px}.sum{margin:12px 0;padding:12px;background:#f5f6f8;border-radius:8px}
table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #e3e5e8;padding:8px;text-align:left;font-size:13px}
.ok{color:#0a7d32}.bad{color:#c62828}.warn{color:#b26a00}
ul{padding-left:18px}li{margin:4px 0;font-size:13px;list-style:none}
"""

_ROW = (
    "<tr><td>{name}</td><td>{file}</td><td>{module}</td><td>{prio}</td>"
    "<td class='{cls}'>{verdict}</td></tr>"
)
_STEP = "<li class='{cls}'>{mark} {title} — {detail}</li>"


def _verdict(r) -> tuple[str, str]:
    if r.passed:
        return "ok", "通过"
    mapping = {
        "assertion": ("bad", "失败"),
        "config": ("bad", "配置错误"),
        "environment": ("warn", "环境异常"),
        "ui_unsupported": ("warn", "UI未支持(M2)"),
        "empty": ("warn", "空场景"),
    }
    return mapping.get(r.error_class, ("bad", "失败"))


def render_html(report: RunReport, out_path: Path | str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows, details = [], []
    for r in report.results:
        cls, verdict = _verdict(r)
        rows.append(
            _ROW.format(
                name=_html.escape(r.scenario.scenario),
                file=_html.escape(r.scenario.file),
                module=_html.escape(r.scenario.module),
                prio=r.scenario.priority.value,
                cls=cls,
                verdict=verdict,
            )
        )
        lis = "".join(
            _STEP.format(
                cls="ok" if s.passed else cls,
                mark="✓" if s.passed else "✗",
                title=_html.escape(s.title),
                detail=_html.escape(s.detail),
            )
            for s in r.steps
        )
        details.append(f"<h3>{_html.escape(r.scenario.scenario)}（env={_html.escape(r.env)}，{r.duration_ms}ms）</h3><ul>{lis}</ul>")
    err_lines = "".join(f"<li class='warn'>{_html.escape(e)}</li>" for e in report.load_errors)
    doc = (
        "<!doctype html><html lang=zh><head><meta charset=utf-8><title>atk 报告</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>atk 测试报告 · env={_html.escape(report.env_name)} · {report.started_at}</h1>"
        f"<div class=sum>共 {report.total} 个场景："
        f"<span class=ok>通过 {report.passed_count}</span> / "
        f"<span class=bad>用例失败 {report.failed_count}</span> / "
        f"<span class=warn>受阻 {report.blocked_count}</span>（环境异常 {report.environment_errors}）</div>"
        + (f"<ul>{err_lines}</ul>" if err_lines else "")
        + "<table><tr><th>场景</th><th>文件</th><th>模块</th><th>优先级</th><th>结论</th></tr>"
        f"{''.join(rows)}</table>"
        f"<h2>步骤明细</h2>{''.join(details)}"
        "</body></html>"
    )
    out_path.write_text(doc, encoding="utf-8")
    return out_path
