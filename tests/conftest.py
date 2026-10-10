from pathlib import Path

import httpx
import pytest

from examples.demo_app import make_server as make_demo
from examples.mock_server import make_server


@pytest.fixture(scope="session")
def mock_base_url():
    srv = make_server(0)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def mock_client(mock_base_url):
    with httpx.Client(base_url=mock_base_url, timeout=10, trust_env=False) as c:
        yield c


@pytest.fixture(scope="session")
def demo_base_url():
    srv = make_demo(0)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def demo_client(demo_base_url):
    with httpx.Client(base_url=demo_base_url, timeout=10, trust_env=False) as c:
        yield c


@pytest.fixture
def human_approval(monkeypatch, tmp_path):
    """模拟"开发者本人在终端"：放行 isatty 并铸出一枚真凭证。

    审批凭证的机械判据是交互式终端（approvals.human_present）。测试里不模拟 TTY 而是
    直接放行它，是为了保留 mint → 绑定对象与断言面 → 核销失效 这整条真实链路：
    换成假 token 或把 human_present 永远返回 True 都会让这套测试失去意义。
    """
    from atk import approvals

    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    def make(target: str, key: str | None = None, project: str = ".") -> str:
        bind = key if key is not None else approvals.expect_hash(target)
        token, _ = approvals.mint(target=approvals._rel(Path(target), Path(project)),
                                  key=bind, project=project)
        return token

    return make


@pytest.fixture
def approve_library(monkeypatch):
    """把临时项目场景库里尚未批准的存量场景一次性批掉（CLI --grandfather 的测试侧等价物）。

    沿袭豁免删除后，gate 要求记录里每个场景的断言面都有签名审批；存量库的一次性
    迁移就是这条路径。返回写入的条目列表。
    """
    from atk import approvals
    from atk.layout import scenarios_dir
    from atk.store.loader import load_scenarios

    monkeypatch.setattr(approvals, "human_present", lambda: True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    def _do(project, by: str = "qa"):
        root = Path(project)
        scs, _errors = load_scenarios(scenarios_dir(root))
        targets = []
        for s in scs:
            if not s.file:
                continue
            key = approvals.expect_hash(s.file)
            if key and not approvals.is_approved(target=s.file, project=root):
                targets.append((approvals._rel(Path(s.file), root), key))
        if not targets:
            return []
        return approvals.grandfather(targets=targets, by=by, project=root)

    return _do

