from atk.executors.env import EnvConfig, load_env, substitute


def test_substitute_deep():
    variables = {"token": "t1", "qty": 2}
    obj = {"h": "Bearer ${token}", "n": ["${token}", 3], "b": {"q": "${qty}"}}
    assert substitute(obj, variables) == {"h": "Bearer t1", "n": ["t1", 3], "b": {"q": 2}}


def test_substitute_keeps_unknown_placeholder():
    assert substitute("${nope}", {}) == "${nope}"
    assert substitute("a-${nope}-b", {}) == "a-${nope}-b"


def test_load_env(tmp_path):
    p = tmp_path / "environments.yaml"
    p.write_text(
        "local:\n  base_url: http://x\n  vars:\n    a: '1'\nstaging:\n  base_url: http://y\n"
    )
    env = load_env(p, "local")
    assert isinstance(env, EnvConfig)
    assert env.base_url == "http://x" and env.vars == {"a": "1"}
    assert load_env(p, "staging").vars == {}
