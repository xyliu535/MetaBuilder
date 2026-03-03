"""
meta_builder.py

build meta.json for every cases

"""
import json
from pathlib import Path
from typing import Dict, Set, Tuple
from file_filter import *
from datasets import load_dataset
from file_filter import FileFilter
import datasets
from comparison_analyzer import ComparisonAnalyzer
from symbol_locator import SymbolLocator
from ratios_computer import RatiosComputer
from repo_maintainer import *
import shutil


datasets.disable_progress_bar()

ds = load_dataset("princeton-nlp/SWE-bench_Verified")
test_data = ds['test']

def copy_touched_code_files(case_dir: Path, meta: Dict):
    """
    Copy all touched code files (from PR and model) into the case_dir,
    preserving repo-relative paths.
    """
    base_info = meta["base_info"]
    repo = RepoMaintainer(
        repo_name=base_info["repo_name"],
        base_commit=base_info["base_commit"]
    )
    repo_root = repo.prepare()

    pr_files = set(meta["pr_patch_info"]["touched_files"]["code_files"])
    model_files = set(meta["model_patch_info"]["touched_files"]["code_files"])
    all_files = pr_files | model_files

    copied = 0
    skipped = 0

    for file in sorted(all_files):
        src = repo_root / file
        dst = case_dir / file
        if not src.exists():
            print(f"[WARN] file not found in repo: {file}")
            skipped += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    print(f"[INFO] Copied {copied} files, skipped {skipped} missing files for {case_dir.name}")
def get_patch_info(diff_text: str, file_filter: FileFilter) -> Dict:
    """
    Parse unified diff and classify files using FileFilter.

    - added_lines / removed_lines: only for CODE files
    - records modified CODE / TEST / DOC / TRASH files separately
    - records block-level change info for CODE files:
        file_change_blocks: {
          file: [
            { "type": "delete_or_modify" | "pure_add", "anchor": int | None }
          ]
        }
    """

    added = 0
    removed = 0

    code_files = set()
    test_files = set()
    docs_files = set()
    trash_files = set()

    current_file: str | None = None
    current_file_type: FileType | None = None

    # file -> list of change blocks
    file_change_blocks: dict[str, list[dict]] = {}

    current_old_line: int | None = None
    last_context_old_line: int | None = None

    # block-level state
    in_block = False
    block_has_minus = False
    block_has_plus = False
    block_anchor: int | None = None

    def flush_block():
        nonlocal in_block, block_has_minus, block_has_plus, block_anchor

        if not in_block:
            return

        if current_file is not None and current_file_type == FileType.CODE:
            if block_has_minus:
                block_type = "delete_or_modify"
                anchor = block_anchor
            else:
                block_type = "pure_add"
                anchor = block_anchor

            file_change_blocks.setdefault(current_file, []).append({
                "type": block_type,
                "anchor": anchor
            })

        in_block = False
        block_has_minus = False
        block_has_plus = False
        block_anchor = None

    for line in diff_text.splitlines():
        # ---- hunk header ----
        if line.startswith("@@"):
            flush_block()
            # @@ -a,b +c,d @@
            old_part = line.split(" ")[1]      # "-a,b"
            old_start = int(old_part[1:].split(",")[0])
            current_old_line = old_start
            last_context_old_line = None
            continue

        # ---- file header ----
        if line.startswith("+++ b/"):
            flush_block()
            path = line[6:].strip()
            current_file = path
            current_file_type = file_filter.classify(path)

            if current_file_type == FileType.CODE:
                code_files.add(path)
            elif current_file_type == FileType.TEST:
                test_files.add(path)
            elif current_file_type == FileType.DOC:
                docs_files.add(path)
            elif current_file_type == FileType.TRASH:
                trash_files.add(path)

            continue

        # ---- ignore other diff headers ----
        if line.startswith("+++ ") or line.startswith("--- "):
            continue

        # ---- only care about CODE files below ----
        if current_file is None or current_file_type != FileType.CODE:
            continue

        # ---- removed line ----
        if line.startswith("-"):
            removed += 1

            if not in_block:
                in_block = True
                block_anchor = current_old_line
            block_has_minus = True

            if current_old_line is not None:
                current_old_line += 1
            continue

        # ---- added line ----
        if line.startswith("+"):
            added += 1

            if not in_block:
                in_block = True
                # pure-add block: try to anchor to nearest context line
                block_anchor = last_context_old_line
            block_has_plus = True
            # + does NOT advance old_line
            continue

        # ---- context line ----
        if current_old_line is not None:
            flush_block()
            last_context_old_line = current_old_line
            current_old_line += 1

    flush_block()

    changed_blocks_count = sum(
        len(blocks) for blocks in file_change_blocks.values()
    )

    pure_add_count = sum(
        1
        for blocks in file_change_blocks.values()
        for block in blocks
        if block.get("type") == "pure_add"
    )

    return {
        "added_lines": added,
        "removed_lines": removed,
        "total_changed": added + removed,
        "has_test_modification": len(test_files) > 0,
        "has_docs_modification": len(docs_files) > 0,
        # "touched_other_file_count": len(test_files) + len(docs_files)
        # "touched_all_file_count": len(code_files) + len(test_files) + len(docs_files)
        "touched_code_file_count": len(code_files),
        "touched_class_count": None,
        "touched_method_count": None,
        "changed_blocks_count": changed_blocks_count,
        "pure_add_count": pure_add_count,
        "touched_files": {
            "code_files": sorted(code_files),
            "test_files": sorted(test_files),
            "docs_files": sorted(docs_files),
            "trash_files": sorted(trash_files),
        },
        "touched_classes": None,
        "touched_methods": None,
        "file_change_blocks": file_change_blocks,
    }


def get_comparison(pr: Dict, model: Dict) -> Dict:
    pr_files = set(pr["touched_files"]["code_files"])
    model_files = set(model["touched_files"]["code_files"])

    return {
        "file_overlap": {
            "intersection": sorted(pr_files & model_files),
            "only_in_pr": sorted(pr_files - model_files),
            "only_in_model": sorted(model_files - pr_files),
        },
        "class_overlap": None,
        "method_overlap": None,
    }


def get_base_info(case_dir: Path) -> Dict | None:
    instance_id = case_dir.name
    if instance_id is not None:
        # Search SWE-Bench Verified for data
        try:
            data = test_data.filter(lambda x: x['instance_id'] == instance_id)
            if len(data) > 0:
                case = data[0]
                if case is None:
                    print(f"[SKIP] {instance_id} not found in SWEBench test split")
                    return None
        except Exception as e:
            print({'found': False, 'message': f"搜索出错: {str(e)}"})

        repo_name = case["repo"]
        base_commit = case["base_commit"]
        pr_id = (case["instance_id"]).split("-")[-1]
        return {
            "instance_id": case_dir.name,
            "repo_name": repo_name,
            "base_commit": base_commit,
            "repo_url": "https://github.com/" + repo_name,
            "PR_url": "https://github.com/" + repo_name + "/pull/" + pr_id,
        }
    else:
        return None


def build_meta_for_case(case_dir: Path) -> bool:
    pr_path = case_dir / "PR_patch.diff"
    model_path = case_dir / "Model_patch.diff"

    if not pr_path.exists() or not model_path.exists():
        print(f"[SKIP] {case_dir.name} missing patch file")
        return False

    pr_text = pr_path.read_text(encoding="utf-8", errors="ignore")
    model_text = model_path.read_text(encoding="utf-8", errors="ignore")

    base_info = get_base_info(case_dir)

    file_filter = FileFilter(repo=base_info["repo_name"])
    pr_info = get_patch_info(pr_text,file_filter)
    model_info = get_patch_info(model_text,file_filter)

    comparison = get_comparison(pr_info, model_info)

    meta = {
        "base_info": base_info,
        "pr_patch_info": pr_info,
        "model_patch_info": model_info,
        "comparison": comparison,
        "ratios": None
        # ratios 留给ratios_computer来写
    }

    with open(case_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"[OK] {case_dir.name}")
    return True




def main():
    cases_dir = Path(__file__).parent.parent / "cases"

    if not cases_dir.exists():
        raise RuntimeError("cases/ directory not found")

    built = 0
    located = 0
    compared = 0
    computed = 0

    for case_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        try:
            # ===== 0. build patch-level meta =====
            if build_meta_for_case(case_dir):
                built += 1

            meta_path = case_dir / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

            # ===== 1. copy touched code files =====
            copy_touched_code_files(case_dir, meta)

            # ===== 2. symbol locate =====
            meta = SymbolLocator(case_dir, meta).analyze()
            located += 1

            # ===== 3. comparison =====
            meta = ComparisonAnalyzer(meta).analyze()
            compared += 1

            # ===== 4. ratios =====
            meta = RatiosComputer(meta).compute()
            computed += 1

            # ===== 5. write back =====
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )


            print(f"[OK] {case_dir.name}")

        except Exception as e:
            print(f"[SKIP] {case_dir.name}: {e}")

    print("\n========== Summary ==========")
    print(f"Meta built: {built}")
    print(f"Symbol located: {located}")
    print(f"Comparison analyzed: {compared}")
    print(f"Ratio computed: {computed}")

if __name__ == "__main__":
    main()
