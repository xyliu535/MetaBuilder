"""
symbol_locator.py

Locate touched classes and methods based on meta.json
and file_change_blocks produced by meta_builder.

Designed to be used in the meta building pipeline.
"""

import ast
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from repo_maintainer import RepoMaintainer


# =======================
# Symbol data structure
# =======================

class Symbol:
    def __init__(self, name: str, kind: str, start: int, end: int):
        self.name = name
        self.kind = kind  # "class" or "method"
        self.start = start
        self.end = end

    def contains(self, line: int) -> bool:
        return self.start <= line <= self.end


# =======================
# AST symbol collector
# =======================

class SymbolCollector(ast.NodeVisitor):
    """
    Collect class / function symbols with line ranges.
    """

    def __init__(self):
        self.symbols: List[Symbol] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        self.symbols.append(Symbol(node.name, "class", start, end))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        self.symbols.append(Symbol(node.name, "method", start, end))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.visit_FunctionDef(node)


# =======================
# Main analyzer
# =======================

class SymbolLocator:
    """
    Analyzer that fills:
    - touched_classes
    - touched_methods
    - touched_class_count
    - touched_method_count

    for both pr_patch and model_patch.
    """

    def __init__(self, case_dir: Path, meta: Dict):
        self.case_dir = case_dir
        self.meta = meta

        base = meta["base_info"]

        self.repo = RepoMaintainer(
            repo_name=base["repo_name"],
            base_commit=base["base_commit"],
        )
        self.repo_root = self.repo.prepare()

    def _collect_symbols(self, file_path: Path) -> List[Symbol]:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            return []

        collector = SymbolCollector()
        collector.visit(tree)
        return collector.symbols

    def _locate_anchor(
        self,
        symbols: List[Symbol],
        anchor: Optional[int],
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Return (class_name, method_name) enclosing the anchor line.
        """
        if anchor is None:
            return None, None

        enclosing = [s for s in symbols if s.contains(anchor)]
        if not enclosing:
            return None, None

        # inner-most symbol wins
        enclosing.sort(key=lambda s: (s.end - s.start))

        cls = None
        method = None
        for s in enclosing:
            if s.kind == "class":
                cls = s.name
            elif s.kind == "method":
                method = s.name

        return cls, method

    def analyze(self) -> Dict:
        """
        Update meta in-place and return it.
        """
        for patch_key in ("pr_patch_info", "model_patch_info"):
            patch = self.meta[patch_key]

            touched_classes = set()
            touched_methods = set()

            file_blocks = patch.get("file_change_blocks", {})
            for file, blocks in file_blocks.items():
                file_path = self.repo_root / file
                if not file_path.exists():
                    continue

                symbols = self._collect_symbols(file_path)

                for block in blocks:
                    anchor = block.get("anchor")
                    cls, method = self._locate_anchor(symbols, anchor)

                    if cls:
                        touched_classes.add(f"{file}::{cls}")
                    if method:
                        touched_methods.add(f"{file}::{method}")

            patch["touched_classes"] = sorted(touched_classes)
            patch["touched_methods"] = sorted(touched_methods)
            patch["touched_class_count"] = len(touched_classes)
            patch["touched_method_count"] = len(touched_methods)

        return self.meta
