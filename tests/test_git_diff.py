import subprocess

import pytest

from atk.diff_analyzer.git_diff import changed_files


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def tiny_repo(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _git(
        "-c", "user.email=t@t", "-c", "user.name=t",
        "commit", "--allow-empty", "-m", "init", cwd=tmp_path,
    )
    (tmp_path / "b.txt").write_text("hello")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add b", cwd=tmp_path)
    return tmp_path


def test_changed_files_between_refs(tiny_repo):
    assert changed_files("HEAD~1", "HEAD", repo=str(tiny_repo)) == ["b.txt"]


def test_changed_files_bad_ref_raises(tiny_repo):
    with pytest.raises(RuntimeError):
        changed_files("no-such-ref", "HEAD", repo=str(tiny_repo))


def test_changed_files_empty_diff(tiny_repo):
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "e", cwd=tiny_repo)
    assert changed_files("HEAD~1", "HEAD", repo=str(tiny_repo)) == []
