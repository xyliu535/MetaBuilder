"""
comparison_analyzer.py

add more detail comparison info for cases

Designed to be used in the meta building pipeline.
"""

from typing import Dict, Set


class ComparisonAnalyzer:
    def __init__(self, meta: Dict):
        self.meta = meta

        self.pr_patch = meta["pr_patch_info"]
        self.model_patch = meta["model_patch_info"]

    def analyze(self) -> Dict:
        """
        Populate meta["comparison"] with overlap information.
        """
        pr_files = self._get_set(self.pr_patch, ["touched_files", "code_files"])
        model_files = self._get_set(self.model_patch, ["touched_files", "code_files"])

        pr_classes = self._get_set(self.pr_patch, ["touched_classes"])
        model_classes = self._get_set(self.model_patch, ["touched_classes"])

        pr_methods = self._get_set(self.pr_patch, ["touched_methods"])
        model_methods = self._get_set(self.model_patch, ["touched_methods"])

        file_overlaps = self._compare_sets(pr_files, model_files)
        class_overlaps = self._compare_sets(pr_classes, model_classes)
        method_overlaps = self._compare_sets(pr_methods, model_methods)

        self.meta["comparison"] = {
            "file_overlap": file_overlaps,
            "class_overlap": class_overlaps,
            "method_overlap": method_overlaps,

            "file_overlap_count": len(file_overlaps["intersection"]),
            "class_overlap_count": len(class_overlaps["intersection"]),
            "method_overlap_count": len(method_overlaps["intersection"]),
        }

        return self.meta

    def _compare_sets(self, pr_set: Set[str], model_set: Set[str]) -> Dict:
        """
        Generic set overlap comparison.
        """
        return {
            "intersection": sorted(pr_set & model_set),
            "only_in_pr": sorted(pr_set - model_set),
            "only_in_model": sorted(model_set - pr_set),
        }

    def _get_set(self, patch: Dict, path: list[str]) -> Set[str]:
        """
        Safely extract a nested list from patch dict and convert to set.
        """
        cur = patch
        for key in path:
            cur = cur.get(key, [])
        return set(cur or [])
