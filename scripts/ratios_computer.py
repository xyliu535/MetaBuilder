"""
ratios_computer.py

Compute quantitative comparison metrics between the human PR patch
and the model-generated patch based on meta.json.

Designed to be used in the meta building pipeline.
"""
from typing import Dict

__all__ = ["RatiosComputer"]


def safe_ratio(num, den):
    if den == 0 or num is None or den is None:
        return 0
    else:
        return round(num / den, 2)

class RatiosComputer:
    def __init__(self, meta: Dict):
        self.meta = meta
        self.pr = self.meta["pr_patch_info"]
        self.model = self.meta["model_patch_info"]

    def added_ratio(self):
        return safe_ratio(self.model["added_lines"], self.pr["added_lines"])

    def removed_ratio(self):
        return safe_ratio(self.model["removed_lines"], self.pr["removed_lines"])

    def total_ratio(self):
        return safe_ratio(self.model["total_changed"], self.pr["total_changed"])

    def touched_file_ratio(self):
        return safe_ratio(self.model["touched_code_file_count"], self.pr["touched_code_file_count"])

    def change_blocks_ratio(self):
        return safe_ratio(self.model["changed_blocks_count"], self.pr["changed_blocks_count"])

    def pure_add_ratio(self):
        return safe_ratio(self.model["pure_add_count"], self.pr["pure_add_count"])

    def touched_class_ratio(self):
        return safe_ratio(self.model["touched_class_count"], self.pr["touched_class_count"])

    def touched_method_ratio(self):
        return safe_ratio(self.model["touched_method_count"], self.pr["touched_method_count"])

    def class_overlap_ratio(self):
        over_lap = self.meta["comparison"]["class_overlap_count"]
        return safe_ratio(over_lap, self.pr["touched_class_count"])

    def method_overlap_ratio(self):
        over_lap = self.meta["comparison"]["method_overlap_count"]
        return safe_ratio(over_lap, self.pr["touched_method_count"])

    def file_overlap_ratio(self):
        over_lap = self.meta["comparison"]["file_overlap_count"]
        return safe_ratio(over_lap, self.pr["touched_code_file_count"])

    def compute(self):
        meta = self.meta
        meta["ratios"] = {
            "added_ratio": self.added_ratio(),
            "removed_ratio": self.removed_ratio(),
            "total_ratio": self.total_ratio(),
            "change_blocks_ratio": self.change_blocks_ratio(),
            "touched_file_ratio": self.touched_file_ratio(),
            "touched_class_ratio": self.touched_class_ratio(),
            "touched_method_ratio": self.touched_method_ratio(),
            "pure_add_ratio": self.pure_add_ratio(),
            "file_overlap_ratio": self.file_overlap_ratio(),
            "class_overlap_ratio": self.class_overlap_ratio(),
            "method_overlap_ratio": self.method_overlap_ratio(),
        }

        return meta

