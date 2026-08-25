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
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    f = sc_dir / "s.yaml"
    f.write_text('scenario: 断网\nsteps:\n  - api:\n      call: "GET /ping"\n')
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=sc_dir)
    report = runner.run(env_name="dead")
    assert report.results[0].error_class == "environment"
    assert report.environment_errors == 1
    # 环境受阻不算用例失败，退出码为 2
    assert report.failed_count == 0
    assert report.blocked_count == 1
    assert report.exit_code == 2


def test_no_variable_leak_between_scenarios(tmp_path, mock_base_url):
    (tmp_path / "environments.yaml").write_text(
        f"it:\n  base_url: {mock_base_url}\n  vars: {{test_sku: SKU-001}}\n"
    )
    (tmp_path / "a.yaml").write_text(
        "scenario: A捕获\nmodule: a\n"
        "steps:\n"
        "  - api:\n"
        '      call: "POST /api/orders"\n'
        "      headers: {Authorization: 'Bearer tok_demo123'}\n"
        "      body: {skuId: '${test_sku}', qty: 1}\n"
        "      expect: {status: 200}\n"
        "      capture: {orderNo: data.orderNo}\n"
    )
    (tmp_path / "b.yaml").write_text(
        "scenario: B不应看到A的变量\nmodule: b\n"
        "steps:\n"
        "  - api:\n"
        '      call: "GET /api/orders?orderNo=${orderNo}"\n'
        "      headers: {Authorization: 'Bearer tok_demo123'}\n"
        "      expect: {status: 200, data.list.0.status: 待支付}\n"
    )
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=tmp_path)
    report = runner.run(env_name="it")
    b = next(r for r in report.results if r.scenario.module == "b")
    assert not b.passed, "B 场景若通过说明 A 的变量泄漏了"


def test_per_scenario_env_when_env_name_none(tmp_path, mock_base_url):
    (tmp_path / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n"
        f"staging:\n  base_url: {mock_base_url}\n"
    )
    (tmp_path / "s1.yaml").write_text(
        'scenario: 本地场景\nenv: local\nsteps:\n  - api:\n      call: "GET /ping"\n'
        "      expect: {status: 200}\n"
    )
    (tmp_path / "s2.yaml").write_text(
        'scenario: 预发场景\nenv: staging\nsteps:\n  - api:\n      call: "GET /ping"\n'
        "      expect: {status: 200}\n"
    )
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=tmp_path)
    report = runner.run(env_name=None)
    assert report.total == 2 and report.passed_count == 2
    assert {r.env for r in report.results} == {"local", "staging"}


def test_exit_code_semantics(tmp_path, mock_base_url):
    _prepare(tmp_path, mock_base_url)
    runner = Runner(env_file=tmp_path / "environments.yaml", scenarios_root=tmp_path)
    report = runner.run(env_name="it")
    assert report.failed_count == 1 and report.exit_code == 1
