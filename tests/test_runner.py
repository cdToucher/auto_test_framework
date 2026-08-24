from pathlib import Path

from atk.executors.runner import Runner

SCENARIOS = {
    "order/create.yaml": (
        "scenario: 下单链路\nmodule: order\npriority: P0\n"
        "steps:\n"
        "  - api:\n"
        '      call: "POST /api/login"\n'
        "      body: {username: alice, password: secret123}\n"
        "      expect: {status: 200}\n"
        "      capture: {token: data.token}\n"
        "  - api:\n"
        '      call: "POST /api/orders"\n'
        "      headers: {Authorization: 'Bearer ${token}'}\n"
        "      body: {skuId: '${test_sku}', qty: 1}\n"
        "      expect: {status: 200, data.orderNo: not_null}\n"
    ),
    "user/broken.yaml": (
        "scenario: 必败用例\nmodule: user\npriority: P1\n"
        "steps:\n"
        "  - api:\n"
        '      call: "POST /api/login"\n'
        "      body: {username: a, password: b}\n"
        "      expect: {status: 200}\n"
    ),
}


def _prepare(tmp_path: Path, base_url: str):
    (tmp_path / "environments.yaml").write_text(
        f"it:\n  base_url: {base_url}\n  vars: {{test_sku: SKU-001}}\n", encoding="utf-8"
    )
    for rel, text in SCENARIOS.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")


def test_runner_runs_and_classifies(tmp_path, mock_base_url):
    _prepare(tmp_path, mock_base_url)
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=tmp_path)
    report = runner.run(env_name="it", module=None)
    assert report.total == 2
    by_name = {r.scenario.scenario: r for r in report.results}
    assert by_name["下单链路"].passed
    assert not by_name["必败用例"].passed
    assert by_name["必败用例"].error_class == "assertion"
    assert by_name["必败用例"].steps[0].passed is False
    assert report.passed_count == 1


def test_runner_stops_at_failed_step_within_scenario(tmp_path, mock_base_url):
    _prepare(tmp_path, mock_base_url)
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=tmp_path)
    report = runner.run(env_name="it")
    broken = next(r for r in report.results if r.scenario.scenario == "必败用例")
    assert len(broken.steps) == 1


def test_runner_environment_error(tmp_path):
    (tmp_path / "environments.yaml").write_text("dead:\n  base_url: http://127.0.0.1:1\n")
    f = tmp_path / "s.yaml"
    f.write_text('scenario: 断网\nsteps:\n  - api:\n      call: "GET /ping"\n')
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=tmp_path)
    report = runner.run(env_name="dead")
    assert report.results[0].error_class == "environment"
    assert report.environment_errors == 1
