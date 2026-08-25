import subprocess
import sys
import textwrap

from typer.testing import CliRunner

from atk.cli import app
from atk.solidify.doctor import diagnose

PENDING = '''
def test_x():
    raise NotImplementedError("UI step pending solidify: 登录")
'''

SUSPECT = '''
def test_y():
    assert 1 == 2, "status 期望 200 实际 500"
'''

HEALTHY = '''
def test_z():
    assert True
'''


def _mk(tmp_path, body):
    f = tmp_path / "gen_test.py"
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return f


def test_diagnose_classifications(tmp_path):
    assert diagnose(_mk(tmp_path, HEALTHY))["status"] == "healthy"
    assert diagnose(_mk(tmp_path, PENDING))["status"] == "pending"
    d = diagnose(_mk(tmp_path, SUSPECT))
    assert d["status"] == "suspect_bug"
    assert d["failures"] and "200" in d["failures"][0]


def test_doctor_command_exit_codes(tmp_path):
    healthy = _mk(tmp_path, HEALTHY)
    suspect = _mk(tmp_path, SUSPECT)
    r1 = CliRunner().invoke(app, ["doctor", str(healthy)])
    assert r1.exit_code == 0
    r2 = CliRunner().invoke(app, ["doctor", str(suspect)])
    assert r2.exit_code == 1
