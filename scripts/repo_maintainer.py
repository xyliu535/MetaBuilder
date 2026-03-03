"""
repo_maintainer.py

Responsibility:
- Ensure a GitHub repo exists locally
- Ensure it is checked out at a specific commit

Public API:
- RepoMaintainer(repo_name, base_commit).prepare() -> Path

Designed to be used in the meta building pipeline.
"""

import subprocess
from pathlib import Path

REPOS_DIR = Path(__file__).resolve().parent.parent / "repos"

__all__ = ["RepoMaintainer"]


class RepoMaintainer:
    def __init__(self, repo_name: str, base_commit: str):
        """
        repo_name: e.g. "astropy/astropy"
        base_commit: commit hash to check out
        """
        self.repo_name = repo_name
        self.base_commit = base_commit

        self.repo_url = self._repo_name_to_url(repo_name)
        self.repo_dir = self._repo_name_to_dir(repo_name)

    def _repo_name_to_url(self, repo_name: str) -> str:
        return f"https://github.com/{repo_name}.git"

    def _repo_name_to_dir(self, repo_name: str) -> Path:
        # only use repo slug for local directory
        slug = repo_name.split("/")[-1].lower()
        return REPOS_DIR / slug

    def _ensure_repo(self):
        if not self.repo_dir.exists():
            print(f"[CLONE] {self.repo_name}")
            subprocess.run(
                ["git", "clone", self.repo_url, str(self.repo_dir)],
                check=True
            )

    def _checkout_commit(self):
        subprocess.run(
            ["git", "checkout", self.base_commit],
            cwd=self.repo_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,

            check=True
        )

    def prepare(self) -> Path:
        """
        Ensure repo exists and is checked out at base_commit.
        Returns local repo path.
        """
        self._ensure_repo()
        self._checkout_commit()
        return self.repo_dir
