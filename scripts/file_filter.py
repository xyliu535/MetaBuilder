"""
file_filter.py

Centralized file classification policy for SWE-bench analysis.

This module is responsible for deciding:
- whether a file modified in a patch should be treated as CODE
- or excluded as TEST / DOC / TRASH (noise)

Designed to be used in the meta building pipeline.

"""

from enum import Enum
from pathlib import Path
from typing import Optional

__all__ = [
    "FileType",
    "FileFilter",
]


class FileType(Enum):
    """
    Semantic category of a file modified by a patch
    """
    CODE = "code"
    TEST = "test"
    DOC = "doc"
    TRASH = "trash"


class FileFilter:
    """
    File classification engine.

    Parameters
    ----------
    repo : str | None
        Name of the checked-out repository.
        Format: "owner/repo" (e.g. "docker/compose")
    """

    def __init__(
            self,
            repo: str,
    ):
        self.repo = repo
        self.repo_name = repo.split("/")[-1].lower()

    def is_test_file(self, path: str) -> bool:
        """
        Detect test files.
        """
        p = path.lower()
        name = Path(p).name
        return (
                "/test/" in p
                or "/tests/" in p
                or name.startswith("test_")
                or name.endswith("_test.py")
        )

    def is_docs_file(self, path: str) -> bool:
        """
        Detect documentation files.
        """
        p = path.lower()
        return (
                p.startswith("docs/")
                or "/docs/" in p
        )

    def is_trash_file(self, path: str) -> bool:
        """
        Detect agent / workflow noise based on repo structure.

        Trash if:
        - file is directly under repo root
        - or top-level directory is not <repo_slug>, docs, or tests
        """
        p = Path(path)

        # 文件直接在 repo 根目录
        if len(p.parts) == 1:
            return True

        top_level = p.parts[0].lower()

        allowed_top_levels = {
            self.repo_name,
            "docs",
            "tests",

            # 有些库的 code 文件不在同名文件夹下
            "lib",
            "src",
            "sklearn"
        }

        return top_level not in allowed_top_levels

    def classify(self, path: str) -> FileType:
        """
        Classify a file path into FileType.

        Order matters:
        1. Non-existent files (relative to repo) -> TRASH
        2. Test files
        3. Docs files
        4. Explicit trash patterns
        5. Otherwise -> CODE
        """

        if self.is_test_file(path):
            return FileType.TEST

        if self.is_docs_file(path):
            return FileType.DOC

        if self.is_trash_file(path):
            return FileType.TRASH

        return FileType.CODE
