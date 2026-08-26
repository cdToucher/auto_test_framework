"""reschedule 独立模块：避免 routes↔app 循环导入。"""
from . import schedules as _sch


def reschedule(app) -> None:
    """按 config/schedules.yaml 重建调度任务。"""
    sched = app.state.scheduler
    if not sched.running:
        return
    sched.remove_all_jobs()
    for t in _sch.load_tasks(app.state.project_root):
        trigger = _sch.cron_trigger_of(t)
        if not trigger:
            continue
        sched.add_job(
            fire_task, trigger=trigger, id=f"sched:{t['name']}",
            args=[app, dict(t)], replace_existing=True,
        )


def fire_task(app, task: dict) -> None:
    """定时触发：走执行管线；忙时跳过（规格：不做错过补偿）。"""
    import sys

    argv = [sys.executable, "-m", "atk", "run", "--env", str(task["env"])]
    if task.get("module"):
        argv += ["--module", str(task["module"])]
    try:
        app.state.jobs.start(argv, cwd=str(app.state.project_root))
    except Exception as e:
        print(f"[schedule] 任务 {task['name']} 跳过: {e}")
