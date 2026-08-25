from pathlib import Path

import os

from atk.executors.runner import Runner


def test_scenario_data_fixture_merged(tmp_path, mock_base_url):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/environments.yaml").write_text(
        f"it:\n  base_url: {mock_base_url}\n  vars: {{sku_from_env: ENV}}\n", encoding="utf-8"
    )
    fx = tmp_path / "fixtures"
    fx.mkdir()
    (fx / "order.yaml").write_text("sku: SKU-FX\nqty: 2\n", encoding="utf-8")
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    (sc_dir / "s.yaml").write_text(
        "scenario: 用fixture下单\nenv: it\ndata: fixtures/order.yaml\n"
        "steps:\n"
        "  - api:\n"
        '      call: "POST /api/orders"\n'
        "      headers: {Authorization: 'Bearer tok_demo123'}\n"
        "      body: {skuId: '${sku}', qty: '${qty}'}\n"
        "      expect: {status: 200}\n",
        encoding="utf-8",
    )
    os.chdir(tmp_path)
    report = Runner(
        env_file=tmp_path / "config/environments.yaml",
        scenarios_root=tmp_path / "scenarios",
    ).run(env_name=None)
    assert report.total == 1 and report.passed_count == 1, [
        (r.scenario.scenario, r.steps) for r in report.results
    ]


def test_missing_fixture_file_is_error(tmp_path, mock_base_url):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/environments.yaml").write_text(
        f"it:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    (sc_dir / "s.yaml").write_text(
        'scenario: 缺fixture\nenv: it\ndata: fixtures/nope.yaml\n'
        'steps:\n  - api:\n      call: "GET /ping"\n      expect: {status: 200}\n',
        encoding="utf-8",
    )
    os.chdir(tmp_path)
    report = Runner(
        env_file=tmp_path / "config/environments.yaml",
        scenarios_root=tmp_path / "scenarios",
    ).run(env_name=None)
    # fixture 缺失按配置错误处理，不计为用例失败
    assert report.results[0].error_class == "config"
