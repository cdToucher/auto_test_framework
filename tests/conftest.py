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
        bind = key if key is not None else (approvals.expect_hash(target) if target and not
                                            target.startswith("run:") else "")
        token, _ = approvals.mint(target=approvals._rel(Path(target), Path(project))
                                  if not target.startswith("run:") else target,
                                  key=bind, project=project)
        return token

    return make

