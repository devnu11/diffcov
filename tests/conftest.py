import os
import subprocess
from pathlib import Path

import pytest


class Repo:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True, env=env
        ).stdout

    def write(self, path: str, lines: list[str] | str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        text = lines if isinstance(lines, str) else "".join(l + "\n" for l in lines)
        p.write_text(text)

    def commit(self, msg: str, files: dict[str, list[str] | str | None] | None = None, mv: tuple[str, str] | None = None) -> str:
        if mv:
            self.git("mv", *mv)
        for path, content in (files or {}).items():
            if content is None:
                self.git("rm", "-q", path)
            else:
                self.write(path, content)
                self.git("add", path)
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


def numbered(n: int, prefix: str = "line") -> list[str]:
    return [f"{prefix} {i} with enough text to be distinctive" for i in range(1, n + 1)]
