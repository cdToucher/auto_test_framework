from atk.executors.env import EnvConfig, load_env, substitute


def test_substitute_deep():
    variables = {"token": "t1", "qty": 2}
    obj = {"h": "Bearer ${token}", "n": ["${token}", 3], "b": {"q": "${qty}"}}
    assert substitute(obj, variables) == {"h": "Bearer t1", "n": ["t1", 3], "b": {"q": 2}}


def test_substitute_keeps_unknown_placeholder():
    assert substitute("${nope}", {}) == "${nope}"
    assert substitute("a-${nope}-b", {}) == "a-${nope}-b"


def test_env_var_injection(monkeypatch):
    monkeypatch.setenv("ATK_TEST_COOKIE", "token=abc123")
    monkeypatch.setenv("ATK_TEST_NUM", "42")
    assert substitute("Cookie: ${env:ATK_TEST_COOKIE}", {}) == "Cookie: token=abc123"
    assert substitute("${env:ATK_TEST_NUM}", {}) == "42"
    assert substitute("${env:NO_SUCH_VAR_XYZ}", {}) == ""  # 未设置的环境变量解析为空串


def test_load_env(tmp_path):
    p = tmp_path / "environments.yaml"
    p.write_text(
        "local:\n  base_url: http://x\n  vars:\n    a: '1'\nstaging:\n  base_url: http://y\n"
    )
    env = load_env(p, "local")
    assert isinstance(env, EnvConfig)
    assert env.base_url == "http://x" and env.vars == {"a": "1"}
    assert load_env(p, "staging").vars == {}


def test_load_env_resolves_env_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("ATK_SECRET_TOK", "tok123")
    p = tmp_path / "environments.yaml"
    p.write_text("s:\n  base_url: http://x\n  vars:\n    t: '${env:ATK_SECRET_TOK}'\n")
    assert load_env(p, "s").vars == {"t": "tok123"}
