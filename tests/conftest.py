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
