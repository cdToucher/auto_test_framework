"""reschedule 独立模块：避免 routes↔app 循环导入。"""
from . import schedules as _sch


def reschedule(app) -> None:
    """按 config/schedules.yaml 重建调度任务。启动前调用也安全（先清后加）。"""
    sched = app.state.scheduler
    try:
        sched.remove_all_jobs()
    except Exception:
        pass
    try:
        tasks = _sch.load_tasks(app.state.project_root)
    except Exception:
        return
    for t in tasks:
        try:
            trigger = _sch.cron_trigger_of(t)
        except Exception:
            continue
        if not trigger:
            continue
        try:
            sched.add_job(
                fire_task, trigger=trigger, id=f"sched:{t.get('name', '?')}",
                args=[app, dict(t)], replace_existing=True,
            )
        except Exception:
            continue


def fire_task(app, task: dict) -> None:
    """定时触发：走执行管线并写入运行记录；忙时跳过（规格：不做错过补偿）。"""
    import sys

    argv = [sys.executable, "-m", "atk", "run", "--env", str(task["env"]),
            "--record-new"]
    if task.get("module"):
        argv += ["--module", str(task["module"])]
    try:
        app.state.jobs.start(argv, cwd=str(app.state.project_root))
    except Exception as e:
        print(f"[schedule] 任务 {task['name']} 跳过: {e}")
