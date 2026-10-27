import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "guard_main", Path(__file__).parent.parent / ".claude" / "hooks" / "guard_main.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    git("branch", "feat/1-x")
    return tmp_path, git


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -m 'x'",
        "git add . && git commit -m x",
        "FOO=1 git merge feat/1-x",
        "git cherry-pick abc",
        "git push",
        "git push origin main",
        "git push origin HEAD:main",
        "git push -u origin feat/1-x:refs/heads/main",
        "git push --force origin feat/1-x",
        "git push --all",
    ],
)
def test_blocks_on_main(repo, cmd):
    path, _ = repo
    assert guard.check_command(cmd, str(path)) is not None


@pytest.mark.parametrize(
    "cmd",
    [
        "git status",
        "git pull --ff-only",
        "git switch -c feat/2-y",
        "git push -u origin feat/1-x",
        "git push --force-with-lease origin feat/1-x",
        "echo 'git commit' > notes.txt",
    ],
)
def test_allows_on_main(repo, cmd):
    path, _ = repo
    assert guard.check_command(cmd, str(path)) is None


def test_allows_commit_and_push_on_branch(repo):
    path, git = repo
    git("switch", "-q", "feat/1-x")
    assert guard.check_command("git commit -m x && git push", str(path)) is None
    assert guard.check_command("git merge main", str(path)) is None


def test_follows_cd_and_dash_c(repo, tmp_path_factory):
    path, _ = repo
    elsewhere = str(tmp_path_factory.mktemp("other"))
    assert guard.check_command(f"cd {path} && git commit -m x", elsewhere) is not None
    assert guard.check_command(f"git -C {path} commit -m x", elsewhere) is not None
