"""
patch_collector.py

Initialize the directory with cases and repos by copying files

"""
import json
import shutil
import requests
from pathlib import Path
from datasets import load_dataset

# ================== Path Configuration ==================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MODEL_NAME = "20251120_livesweagent_gemini-3-pro-preview"

AGENT_DIR = PROJECT_ROOT / "Model_data" / MODEL_NAME
RESULT_JSON = AGENT_DIR / "results.json"
MODEL_LOGS_DIR = AGENT_DIR / "logs"

CASES_DIR = PROJECT_ROOT / "cases"

REQUEST_TIMEOUT = 20


def load_verified_dataset():
    """
    load SWE-bench Verified test split
    """
    return load_dataset(
        "princeton-nlp/SWE-bench_Verified",
        split="test"
    )

def load_resolved_instance_ids(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "resolved" not in data:
        raise ValueError("result.json 中未找到 'resolved' 字段")

    return data["resolved"]


def main():
    CASES_DIR.mkdir(exist_ok=True)

    resolved_ids = set(load_resolved_instance_ids(RESULT_JSON))
    print(f"[INFO] Resolved cases: {len(resolved_ids)}")

    print("[INFO] Loading SWE-bench_Verified dataset...")
    ds = load_verified_dataset()

    collected = 0
    skipped = 0

    for row in ds:
        instance_id = row["instance_id"]

        if instance_id not in resolved_ids:
            continue

        model_patch_src = MODEL_LOGS_DIR / instance_id / "patch.diff"
        if not model_patch_src.exists():
            print(f"[SKIP] model patch missing: {instance_id}")
            skipped += 1
            continue

        human_patch = row.get("patch", "")
        if not human_patch.strip():
            print(f"[SKIP] empty human patch: {instance_id}")
            skipped += 1
            continue

        case_dir = CASES_DIR / instance_id
        case_dir.mkdir(exist_ok=True)

        # 写人工 patch
        with open(case_dir / "PR_patch.diff", "w", encoding="utf-8") as f:
            f.write(human_patch)

        # 拷贝模型 patch
        shutil.copyfile(model_patch_src, case_dir / "Model_patch.diff")

        collected += 1
        print(f"[OK] {instance_id}")

    print("\n========== Summary ==========")
    print(f"Collected: {collected}")
    print(f"Skipped:   {skipped}")
    print(f"Cases dir: {CASES_DIR.resolve()}")


if __name__ == "__main__":
    main()