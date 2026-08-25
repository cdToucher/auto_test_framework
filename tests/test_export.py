import subprocess
import sys

from atk.solidify.exporter import export_scenario

YAML_SCEN = (
    "scenario: 登录并查询\nmodule: auth\nenv: local\n"
    "steps:\n"
    "  - api:\n"
    '      call: "POST /api/login"\n'
    "      body: {username: alice, password: secret123}\n"
    "      expect: {status: 200}\n"
    "      capture: {token: data.token}\n"
    "  - ui:\n"
    "      action: 用订单号搜索\n"
    "      expect: 出现待支付记录\n"
)

API_ONLY = (
    "scenario: 纯接口场景\nmodule: auth\nenv: local\n"
    "steps:\n"
    "  - api:\n"
    '      call: "GET /ping"\n'
    "      expect: {status: 200, data: pong}\n"
)


def _slug_from(path):
    return path.stem


def test_export_ui_scenario_has_placeholder(tmp_path, mock_base_url):
    src = tmp_path / "login.yaml"
    src.write_text(YAML_SCEN, encoding="utf-8")
    out = tmp_path / "generated"
    path = export_scenario(src, out_dir=out, base_url=mock_base_url)
    assert path.exists() and path.parent == out
    text = path.read_text(encoding="utf-8")
    assert "DO NOT EDIT" in text
    assert "NotImplementedError" in text
    assert "#   操作: 用订单号搜索" in text


def test_export_api_only_passes_pytest(tmp_path, mock_base_url):
    src = tmp_path / "ping.yaml"
    src.write_text(API_ONLY, encoding="utf-8")
    path = export_scenario(src, out_dir=tmp_path / "generated", base_url=mock_base_url)
    code = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert code.returncode == 0, code.stdout + code.stderr


def test_export_full_chain_capture_then_assert(tmp_path, mock_base_url):
    """登录捕获 token -> 创建订单 -> 查询断言，全链固化后 pytest 必须真通过。"""
    scen = (
        "scenario: 全链路\nmodule: order\nenv: local\n"
        "steps:\n"
        "  - api:\n"
        '      call: "POST /api/login"\n'
        "      body: {username: alice, password: secret123}\n"
        "      expect: {status: 200}\n"
        "      capture: {token: data.token}\n"
        "  - api:\n"
        '      call: "POST /api/orders"\n'
        "      headers: {Authorization: 'Bearer ${token}'}\n"
        "      body: {skuId: 'SKU-001', qty: '1'}\n"
        "      expect: {status: 200, data.orderNo: not_null}\n"
        "      capture: {orderNo: data.orderNo}\n"
        "  - api:\n"
        '      call: "GET /api/orders?orderNo=${orderNo}"\n'
        "      headers: {Authorization: 'Bearer ${token}'}\n"
        "      expect: {status: 200, data.list.0.status: 待支付}\n"
    )
    src = tmp_path / "chain.yaml"
    src.write_text(scen, encoding="utf-8")
    path = export_scenario(src, out_dir=tmp_path / "generated", base_url=mock_base_url)
    code = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert code.returncode == 0, code.stdout + code.stderr
