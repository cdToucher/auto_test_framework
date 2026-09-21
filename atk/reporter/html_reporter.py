"""HTML 报告渲染：零模板引擎，f-string 生成单文件。"""
import html as _html
from pathlib import Path

from ..executors.runner import RunReport
from ..run_store import review_status

_CSS = """
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:24px;color:#1a1a1a}
h1{font-size:20px}.sum{margin:12px 0;padding:12px;background:#f5f6f8;border-radius:8px}
table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #e3e5e8;padding:8px;text-align:left;font-size:13px}
.ok{color:#0a7d32}.bad{color:#c62828}.warn{color:#b26a00}.pending{color:#1565c0}
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
        "ui_pending": ("pending", "UI待实测"),
        "skipped": ("pending", "已跳过"),
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
        + (
            f"<div class=sum>UI 待实测 {report.pending_ui_count} 个"
            f"（由 AI 浏览器实测后 atk record 回填，未回填时 gate 拦截）</div>"
            if report.pending_ui_count
            else ""
        )
        + (
            "<div class=sum>"
            + "<br>".join(f"<span class=warn>⚠ {_html.escape(w)}</span>" for w in report.env_warnings)
            + "</div>"
            if report.env_warnings
            else ""
        )
        + (f"<ul>{err_lines}</ul>" if err_lines else "")
        + "<table><tr><th>场景</th><th>文件</th><th>模块</th><th>优先级</th><th>结论</th></tr>"
        f"{''.join(rows)}</table>"
        f"<h2>步骤明细</h2>{''.join(details)}"
        "</body></html>"
    )
    out_path.write_text(doc, encoding="utf-8")
    return out_path


_RUN_STATUS = {
    "pass": ("ok", "通过"),
    "fail": ("bad", "失败"),
    "suspect": ("warn", "疑似"),
    "blocked": ("warn", "受阻"),
    "pending": ("pending", "待实测"),
}


def render_run_html(rec, out_path: Path | str) -> Path:
    """渲染即时层运行记录（RunRecord）为 HTML 报告。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    srows, intents = [], []
    for s in rec.scenarios:
        cls, verdict = ("ok", "通过") if s.passed else ("bad", s.error_class)
        srows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td class='{}'>{}</td></tr>".format(
                _html.escape(s.name), _html.escape(s.file), _html.escape(s.priority),
                cls, _html.escape(str(verdict)),
            )
        )
    for i in rec.intents:
        cls, label = _RUN_STATUS.get(i.status, ("bad", i.status))
        imgs = "".join(
            f"<a href='{_html.escape(e)}'><img src='{_html.escape(e)}' "
            "style='max-width:320px;border:1px solid #ddd;margin:4px'></a>"
            for e in i.evidence
        )
        intents.append(
            f"<li class='{cls}'>[{_html.escape(label)}] {_html.escape(i.title)}"
            + (f" — {_html.escape(i.note)}" if i.note else "")
            + (f"<br>{imgs}" if imgs else "")
            + "</li>"
        )
    passed_n = sum(1 for s in rec.scenarios if s.passed)
    title = getattr(rec, "title", "") or ""
    commits = getattr(rec, "commits", []) or []
    reviews = getattr(rec, "reviews", []) or []
    commit_lines = "".join(
        f"<li><code>{_html.escape(c.short)}</code> {_html.escape(c.subject)}</li>"
        for c in commits
    ) or "<li>（无提交记录）</li>"
    status, last = review_status(reviews)
    if status == "rejected":
        assert last is not None
        review_status_text = (
            f"已被开发驳回 by {_html.escape(last.by)}"
            + (f" — {_html.escape(last.note)}" if last.note else "")
        )
        review_cls = "bad"
    elif status == "approved":
        assert last is not None
        review_status_text = f"已获开发确认 by {_html.escape(last.by)}"
        review_cls = "ok"
    else:
        review_status_text = "未经开发确认"
        review_cls = "warn"
    title_h2 = f"<h2>功能：{_html.escape(title)}</h2>" if title else ""
    doc = (
        "<!doctype html><html lang=zh><head><meta charset=utf-8>"
        f"<title>atk 运行报告 {_html.escape(str(rec.run_id))}</title><style>{_CSS}</style></head><body>"
        f"<h1>即时层运行报告 · {_html.escape(str(rec.run_id))} · {_html.escape(str(rec.created_at))}</h1>"
        f"{title_h2}"
        f"<div class=sum>基线 {_html.escape(rec.base_ref)} → "
        f"{_html.escape(rec.head_ref)}　受影响模块："
        f"{_html.escape(', '.join(rec.affected_modules) or '无')}<br>"
        f"复用场景 {len(rec.scenarios)} 个（通过 {passed_n}），"
        f"探索意图 {len(rec.intents)} 条<br>"
        f"确认状态：<span class='{review_cls}'>{review_status_text}</span></div>"
        "<details open><summary>提交清单"
        f"（{len(commits)}）</summary><ul>{commit_lines}</ul></details>"
        "<details open><summary>受影响文件"
        f"（{len(rec.affected_files)}）</summary><pre>"
        f"{_html.escape(chr(10).join(rec.affected_files))}</pre></details>"
        "<table><tr><th>场景</th><th>文件</th><th>优先级</th><th>结论</th></tr>"
        f"{''.join(srows)}</table>"
        "<h2>探索意图</h2><ul>" + "".join(intents) + "</ul>"
        "</body></html>"
    )
    out_path.write_text(doc, encoding="utf-8")
    return out_path
