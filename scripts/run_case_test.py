"""
run_case_test.py

Run an LLM-authored differentiating test script for one case against a target patch.

Default workflow:
1) Read cases/<case_id>/meta.json for repo_name + base_commit
2) Prepare local repo via RepoMaintainer
3) Create a temporary git worktree at base_commit
4) Optionally apply Model_patch.diff / PR_patch.diff
5) Copy the generated test script into the worktree
6) Prepare dependencies from the checked-out commit
7) Run pytest on that test file
8) Save run artifacts under cases/<case_id>/runs/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_maintainer import RepoMaintainer


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = PROJECT_ROOT / "cases"

RISKY_DEP_UPPER_BOUNDS: dict[str, str] = {
    "numpy": "<2",
    "cython": "<3",
    "setuptools": "<70",
}

REPO_SPECIFIC_GUARDRAILS: dict[str, list[str]] = {
    "astropy/astropy": ["numpy<2"],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
    )


def _python_in_venv(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _ensure_managed_venv(venv_path: Path, base_python: Path | None = None) -> Path:
    py = _python_in_venv(venv_path)
    if py.exists():
        return py

    venv_path.parent.mkdir(parents=True, exist_ok=True)
    creator = str(base_python) if base_python is not None else sys.executable
    cp = subprocess.run(
        [creator, "-m", "venv", str(venv_path)],
        check=False,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            "failed to create managed venv:\n"
            f"stdout:\n{cp.stdout}\n"
            f"stderr:\n{cp.stderr}"
        )
    py = _python_in_venv(venv_path)
    if not py.exists():
        raise RuntimeError(f"managed venv python not found: {py}")
    return py


def _has_module(python_exe: Path, module_name: str, cwd: Path) -> bool:
    cp = subprocess.run(
        [str(python_exe), "-c", f"import {module_name}"],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    return cp.returncode == 0


def _python_version(python_exe: Path) -> tuple[int, int, int] | None:
    cp = subprocess.run(
        [
            str(python_exe),
            "-c",
            "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    if cp.returncode != 0:
        return None
    raw = (cp.stdout or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except ValueError:
        return None


def _discover_python_candidates() -> list[Path]:
    names = [
        "python3.9",
        "python3.10",
        "python3.11",
        "python3.8",
        "python3",
        "python",
    ]
    found: list[Path] = []
    seen: set[str] = set()
    for name in names:
        p = shutil.which(name)
        if not p:
            continue
        rp = str(Path(p).resolve())
        if rp in seen:
            continue
        seen.add(rp)
        found.append(Path(rp))
    return found


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        out.append(Path(rp))
    return out


def _build_base_python_candidates(
    primary: Path,
    python_candidates: list[str],
    auto_python_fallback: bool,
) -> list[Path]:
    candidates: list[Path] = [primary]
    for item in python_candidates:
        candidates.append(Path(item))
    if auto_python_fallback:
        candidates.extend(_discover_python_candidates())
    candidates = _dedupe_paths(candidates)
    valid: list[Path] = []
    for c in candidates:
        if _python_version(c) is not None:
            valid.append(c)
    return valid


def _venv_path_for_base(base_path: str, venv_root: Path) -> Path:
    ver = _python_version(Path(base_path))
    if ver is None:
        return venv_root
    return venv_root.with_name(f"{venv_root.name}_py{ver[0]}{ver[1]}")


def _pip_install(
    python_exe: Path,
    cwd: Path,
    args: list[str],
    run_dir: Path,
    log_prefix: str,
) -> int:
    cp = _run([str(python_exe), "-m", "pip", "install", *args], cwd=cwd, check=False)
    (run_dir / f"{log_prefix}.stdout.txt").write_text(cp.stdout or "", encoding="utf-8")
    (run_dir / f"{log_prefix}.stderr.txt").write_text(cp.stderr or "", encoding="utf-8")
    return cp.returncode


def _load_pyproject(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        import tomllib
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _detect_known_dep_install_issue(run_dir: Path) -> dict[str, str] | None:
    text = ""
    for p in run_dir.glob("pip_*.stderr.txt"):
        text += p.read_text(encoding="utf-8", errors="ignore") + "\n"
    for p in run_dir.glob("pip_*.stdout.txt"):
        text += p.read_text(encoding="utf-8", errors="ignore") + "\n"
    low = text.lower()

    if "no module named 'imp'" in low or 'no module named "imp"' in low:
        return {
            "code": "python_too_new_for_repo_commit",
            "message": (
                "This repo commit uses legacy build code requiring module 'imp'. "
                "Use Python 3.11 or lower with --python-exe."
            ),
        }

    if "module 'collections' has no attribute 'mutablesequence'" in low:
        return {
            "code": "python_too_new_for_legacy_collections_api",
            "message": (
                "This repo commit uses legacy collections API (MutableSequence on collections). "
                "Use Python 3.9 or lower with --python-exe."
            ),
        }

    if "unable to avoid copy while creating an array as requested" in low:
        return {
            "code": "numpy_too_new_for_repo_commit",
            "message": (
                "This repo commit is incompatible with NumPy 2.x copy semantics. "
                "Install with a NumPy < 2 constraint."
            ),
        }

    if "could not open requirements file" in low:
        return {
            "code": "requirements_include_path_broken",
            "message": (
                "A nested requirement/constraint include path could not be resolved. "
                "Relative -r / -c paths may need rewriting."
            ),
        }

    if "ssl" in low and ("certificate" in low or "eof" in low):
        return {
            "code": "pip_ssl_or_mirror_issue",
            "message": "Package index SSL/mirror connection failed during dependency install.",
        }

    if "no matching distribution found" in low:
        return {
            "code": "package_resolution_failed",
            "message": "A required dependency could not be resolved from current package index.",
        }

    return None


def _detect_known_pytest_issue(stderr_text: str) -> dict[str, str] | None:
    low = (stderr_text or "").lower()

    if "module 'collections' has no attribute 'mapping'" in low:
        return {
            "code": "python_too_new_for_legacy_collections_api",
            "message": "Pytest import phase failed due collections.Mapping compatibility; try Python 3.9 or lower.",
        }

    if "module 'collections' has no attribute 'mutablesequence'" in low:
        return {
            "code": "python_too_new_for_legacy_collections_api",
            "message": "Pytest import phase failed due collections.MutableSequence compatibility; try Python 3.9 or lower.",
        }

    if (
        "_pytest/cacheprovider.py" in low
        and "cache_dir" in low
        and "nonetype" in low
    ):
        return {
            "code": "pytest_cacheprovider_incompatible",
            "message": "Pytest cache provider initialization failed; retry with '-p no:cacheprovider' or use an older pytest.",
        }

    return None


def _load_meta(case_dir: Path) -> dict:
    meta_path = case_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _patch_path_for(case_dir: Path, patch_kind: str) -> Path | None:
    if patch_kind == "model":
        return case_dir / "Model_patch.diff"
    if patch_kind == "pr":
        return case_dir / "PR_patch.diff"
    return None


def _safe_case_tag(case_id: str) -> str:
    return case_id.replace("/", "_").replace("\\", "_")


def _normalize_req_name(req: str) -> str:
    raw = req.strip()
    if not raw:
        return ""
    if raw.startswith(("-", "#")):
        return ""
    if raw.startswith(("git+", "http://", "https://", "svn+", "hg+")):
        return ""
    if ";" in raw:
        raw = raw.split(";", 1)[0].strip()

    m = re.match(r"^\s*([A-Za-z0-9_.\-]+)", raw)
    if not m:
        return ""
    return m.group(1).lower().replace("_", "-")


def _has_upper_bound(req: str) -> bool:
    return "<" in req


def _inject_upper_bound(req: str, upper_bound: str) -> str:
    line = req.strip()
    if not line:
        return line
    if ";" in line:
        base, marker = line.split(";", 1)
        base = base.strip()
        marker = marker.strip()
        if _has_upper_bound(base):
            return line
        return f"{base},{upper_bound}; {marker}"
    if _has_upper_bound(line):
        return line
    return f"{line},{upper_bound}"


def _rewrite_requirement_line(line: str, src_file: Path) -> str:
    stripped = line.strip()
    if not stripped:
        return line
    if stripped.startswith("#"):
        return line

    suffix = "\n" if line.endswith("\n") else ""

    include_prefixes = ("-r ", "--requirement ", "-c ", "--constraint ")
    for prefix in include_prefixes:
        if stripped.startswith(prefix):
            target = stripped[len(prefix):].strip()
            if not target:
                return line

            if "://" in target:
                return line

            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (src_file.parent / target_path).resolve()

            return f"{prefix}{target_path}{suffix}"

    if stripped.startswith(("-e ", "--editable ")):
        return line
    if "@" in stripped and "://" in stripped:
        return line

    pkg = _normalize_req_name(stripped)
    if not pkg:
        return line

    upper = RISKY_DEP_UPPER_BOUNDS.get(pkg)
    if not upper:
        return line

    rewritten = _inject_upper_bound(stripped, upper)
    return rewritten + suffix


def _classify_requirement_path(path: Path) -> str:
    name = path.name.lower()
    rel = path.as_posix().lower()

    if "doc" in name or "/doc/" in rel or "/docs/" in rel:
        return "docs"
    if any(k in name for k in ("dev", "test", "testing")):
        return "dev"
    return "base"


def _find_requirement_files(repo_root: Path) -> dict[str, list[Path]]:
    discovered: list[Path] = []

    candidate_names = {
        "requirements.txt",
        "requirements-dev.txt",
        "requirements_dev.txt",
        "requirements-test.txt",
        "requirements_test.txt",
        "requirements-doc.txt",
        "requirements_docs.txt",
        "pip-requirements",
        "pip-requirements-dev",
        "pip-requirements-doc",
        "dev-requirements.txt",
        "test-requirements.txt",
    }

    for name in candidate_names:
        p = repo_root / name
        if p.exists() and p.is_file():
            discovered.append(p)

    glob_patterns = [
        "requirements*.txt",
        "requirements*",
        "pip-requirements*",
        "requirements/*.txt",
        "requirements/*.in",
        "requirements/*",
    ]
    for pattern in glob_patterns:
        for p in repo_root.glob(pattern):
            if p.is_file():
                discovered.append(p)

    deduped: list[Path] = []
    seen: set[str] = set()
    for p in discovered:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        deduped.append(p)

    groups: dict[str, list[Path]] = {
        "base": [],
        "dev": [],
        "docs": [],
    }
    for p in sorted(deduped, key=lambda x: x.as_posix()):
        groups[_classify_requirement_path(p)].append(p)
    return groups


def _build_rewritten_requirements_file(src: Path, run_dir: Path) -> Path:
    rewritten_name = f"rewritten_{src.name.replace('/', '_')}.txt"
    dst = run_dir / rewritten_name

    lines = src.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    out_lines = [_rewrite_requirement_line(line, src) for line in lines]
    dst.write_text("".join(out_lines), encoding="utf-8")
    return dst


def _repo_specific_guardrails(repo_name: str) -> list[str]:
    return list(REPO_SPECIFIC_GUARDRAILS.get(repo_name, []))


def _default_guardrails() -> list[str]:
    return [
        "setuptools<70",
        "wheel",
    ]


def _prepare_dependency_guardrails(
    python_exe: Path,
    repo_root: Path,
    run_dir: Path,
    repo_name: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "steps": [],
        "packages": [],
    }

    pkgs = _default_guardrails() + _repo_specific_guardrails(repo_name)

    ordered: list[str] = []
    seen: set[str] = set()
    for pkg in pkgs:
        if pkg in seen:
            continue
        seen.add(pkg)
        ordered.append(pkg)

    result["packages"] = ordered
    if not ordered:
        return result

    rc = _pip_install(
        python_exe,
        repo_root,
        ordered,
        run_dir,
        "pip_guardrails",
    )
    result["steps"].append({"name": "pip_guardrails", "rc": rc})
    result["ok"] = rc == 0
    return result


def _install_requirements_groups(
    python_exe: Path,
    repo_root: Path,
    run_dir: Path,
    groups: dict[str, list[Path]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "steps": [],
        "discovered": {k: [p.as_posix() for p in v] for k, v in groups.items()},
        "skipped_docs": True,
    }

    install_order = ["base", "dev"]
    for group_name in install_order:
        for src in groups.get(group_name, []):
            rewritten = _build_rewritten_requirements_file(src, run_dir)
            rc = _pip_install(
                python_exe,
                repo_root,
                ["-r", str(rewritten)],
                run_dir,
                f"pip_{group_name}_{src.name.replace('.', '_').replace('-', '_')}",
            )
            result["steps"].append(
                {
                    "group": group_name,
                    "source": src.as_posix(),
                    "rewritten": rewritten.as_posix(),
                    "rc": rc,
                }
            )
            if rc != 0:
                result["ok"] = False
                return result

    return result


def _install_repo_base_package(
    python_exe: Path,
    repo_root: Path,
    run_dir: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "steps": [],
    }

    attempts = [
        ("pip_install_editable", ["-e", "."], "pip_repo_editable"),
        (
            "pip_install_editable_no_build_isolation",
            ["-e", ".", "--no-build-isolation"],
            "pip_repo_editable_no_build_isolation",
        ),
        ("pip_install_regular", ["."], "pip_repo_regular"),
        (
            "pip_install_regular_no_build_isolation",
            [".", "--no-build-isolation"],
            "pip_repo_regular_no_build_isolation",
        ),
    ]
    for step_name, step_args, log_prefix in attempts:
        rc = _pip_install(
            python_exe,
            repo_root,
            step_args,
            run_dir,
            log_prefix,
        )
        result["steps"].append({"name": step_name, "rc": rc})
        if rc == 0:
            result["ok"] = True
            result["selected"] = step_name
            return result

    return result


def _install_repo_deps_for_commit(
    python_exe: Path,
    repo_root: Path,
    run_dir: Path,
    repo_name: str,
) -> dict:
    result: dict[str, Any] = {
        "steps": [],
        "ok": True,
        "base_install_ok": True,
    }

    pyproject = _load_pyproject(repo_root / "pyproject.toml")
    has_packaging = any(
        (repo_root / name).exists()
        for name in ("pyproject.toml", "setup.py", "setup.cfg")
    )

    guardrail_result = _prepare_dependency_guardrails(
        python_exe=python_exe,
        repo_root=repo_root,
        run_dir=run_dir,
        repo_name=repo_name,
    )
    result["guardrails"] = guardrail_result
    result["steps"].append({"name": "guardrails", "rc": 0 if guardrail_result.get("ok") else 1})
    if not guardrail_result.get("ok", False):
        result["ok"] = False
        return result

    req_groups = _find_requirement_files(repo_root)
    result["requirement_files"] = {
        k: [p.as_posix() for p in v]
        for k, v in req_groups.items()
    }

    req_result = _install_requirements_groups(
        python_exe=python_exe,
        repo_root=repo_root,
        run_dir=run_dir,
        groups=req_groups,
    )
    result["requirements_install"] = req_result
    result["steps"].append({"name": "requirements_install", "rc": 0 if req_result.get("ok") else 1})
    if not req_result.get("ok", False):
        result["ok"] = False
        return result

    if has_packaging:
        base_pkg_result = _install_repo_base_package(
            python_exe=python_exe,
            repo_root=repo_root,
            run_dir=run_dir,
        )
        result["base_package_install"] = base_pkg_result
        result["steps"].append({"name": "base_package_install", "rc": 0 if base_pkg_result.get("ok") else 1})

        if not base_pkg_result.get("ok", False):
            result["base_install_ok"] = False
            result["base_install_warning"] = (
                "Base package install failed; continue with best-effort dependency setup."
            )

    extras_to_try: list[str] = []
    if pyproject:
        optional = (
            pyproject.get("project", {}).get("optional-dependencies", {})
            if isinstance(pyproject.get("project", {}), dict)
            else {}
        )
        if isinstance(optional, dict):
            for key in ("test", "tests", "testing", "dev"):
                if key in optional:
                    extras_to_try.append(key)

    if extras_to_try:
        extras_expr = ".[{}]".format(",".join(extras_to_try))
        rc = _pip_install(
            python_exe,
            repo_root,
            ["-e", extras_expr],
            run_dir,
            "pip_repo_extras",
        )
        result["steps"].append({"name": f"pip_install_{extras_expr}", "rc": rc})

    return result


def _run_pytest_once(
    runner_python: Path,
    worktree_path: Path,
    dest_rel: Path,
    pytest_extra_args: list[str],
    run_dir: Path,
    log_prefix: str,
    disable_cacheprovider: bool = False,
) -> subprocess.CompletedProcess:
    pytest_cmd = [str(runner_python), "-m", "pytest", "-q"]
    if disable_cacheprovider:
        pytest_cmd.extend(["-p", "no:cacheprovider"])
    pytest_cmd.extend([dest_rel.as_posix(), *pytest_extra_args])

    cp = _run(pytest_cmd, cwd=worktree_path, check=False)
    (run_dir / f"{log_prefix}.stdout.txt").write_text(cp.stdout or "", encoding="utf-8")
    (run_dir / f"{log_prefix}.stderr.txt").write_text(cp.stderr or "", encoding="utf-8")
    cp.pytest_cmd = pytest_cmd  # type: ignore[attr-defined]
    return cp


def _finalize_result_summary(summary: dict) -> None:
    """
    Add user-facing result classification fields so the final outcome is obvious.
    """
    status = summary.get("status")
    test_exit_code = summary.get("test_exit_code")
    repo_dep_install = summary.get("repo_dep_install") or {}
    repo_dep_ok = repo_dep_install.get("ok")
    apply_success = summary.get("apply_success")
    pytest_issue = summary.get("pytest_issue") or {}
    pytest_retry_cmd = summary.get("pytest_retry_cmd")
    python_attempts = summary.get("python_attempts") or []

    summary["infra_ok"] = False
    summary["pytest_bootstrap_ok"] = False
    summary["tests_reached"] = False
    summary["result_kind"] = "infra_error"
    summary["result_summary"] = "Run did not complete successfully."
    summary["phase"] = "prepare_env"
    summary["retry_notes"] = []

    if pytest_issue.get("code") == "pytest_cacheprovider_incompatible" and pytest_retry_cmd:
        summary["retry_notes"].append(
            "Initial pytest run hit cacheprovider incompatibility; retried with '-p no:cacheprovider'."
        )

    if not apply_success:
        summary["phase"] = "apply_patch"
        summary["result_kind"] = "infra_error"
        summary["result_summary"] = "Patch application failed before tests could run."
    elif status in {
        "repo_dep_install_failed",
        "repo_dep_python_compat_failed",
        "repo_dep_numpy_compat_failed",
        "repo_dep_requirements_include_failed",
        "pip_pytest_failed",
        "pip_editable_failed",
        "apply_failed",
    }:
        summary["phase"] = "install_dependencies"
        summary["result_kind"] = "infra_error"
        summary["result_summary"] = "Environment or dependency setup failed before tests could run."
    else:
        if repo_dep_ok is True or not summary.get("repo_dep_install"):
            summary["infra_ok"] = True

        if test_exit_code is not None:
            summary["pytest_bootstrap_ok"] = True
            summary["tests_reached"] = True
            summary["phase"] = "run_tests"

            if test_exit_code == 0:
                summary["result_kind"] = "test_passed"
                summary["result_summary"] = "Environment setup succeeded and all selected tests passed."
            else:
                summary["result_kind"] = "test_failed"
                summary["result_summary"] = "Environment setup succeeded and tests executed; at least one test failed."
        else:
            summary["phase"] = "pytest_bootstrap"
            summary["result_kind"] = "infra_error"
            summary["result_summary"] = "Pytest did not reach a completed test result."

    if summary["result_kind"] == "infra_error":
        summary["final_label"] = "INFRA_ERROR"
    elif summary["result_kind"] == "test_failed":
        summary["final_label"] = "TEST_FAILED"
    else:
        summary["final_label"] = "TEST_PASSED"

    summary["attempt_count"] = len(python_attempts)
    summary["primary_verdict"] = {
        "label": summary["final_label"],
        "summary": summary["result_summary"],
        "phase": summary["phase"],
        "infra_ok": summary["infra_ok"],
        "tests_reached": summary["tests_reached"],
        "test_exit_code": test_exit_code,
    }

def _ordered_summary_for_output(summary: dict) -> dict:
    """
    Reorder summary keys so the most important verdict fields appear first.
    """
    preferred_order = [
        "final_label",
        "result_kind",
        "result_summary",
        "primary_verdict",
        "phase",
        "infra_ok",
        "pytest_bootstrap_ok",
        "tests_reached",
        "retry_notes",
        "attempt_count",
        "status",
        "test_exit_code",
        "case_id",
        "repo_name",
        "base_commit",
        "patch_kind",
        "patch_file",
        "test_script_src",
        "repo_test_path",
        "started_at",
        "finished_at",
        "duration_seconds",
        "apply_success",
        "runner_python",
        "python_attempts",
        "repo_dep_install",
        "repo_dep_issue",
        "repo_dep_warning",
        "pip_pytest_exit_code",
        "pip_editable_exit_code",
        "pytest_cmd",
        "pytest_retry_cmd",
        "pytest_issue",
    ]

    ordered: dict = {}

    for key in preferred_order:
        if key in summary:
            ordered[key] = summary[key]

    for key, value in summary.items():
        if key not in ordered:
            ordered[key] = value

    return ordered


def run_case(
    case_id: str,
    patch_kind: str,
    test_script_name: str,
    repo_test_path: str | None,
    pytest_extra_args: list[str],
    ensure_pytest: bool,
    install_editable: bool,
    install_repo_deps: bool,
    managed_venv: bool,
    managed_venv_path: str,
    python_exe: str | None,
    auto_python_fallback: bool,
    python_candidates: list[str],
) -> int:
    case_dir = CASES_DIR / case_id
    if not case_dir.exists():
        raise FileNotFoundError(f"case directory not found: {case_dir}")

    test_script_src = case_dir / test_script_name
    if not test_script_src.exists():
        raise FileNotFoundError(f"test script not found: {test_script_src}")

    meta = _load_meta(case_dir)
    base_info = meta.get("base_info") or {}
    repo_name = base_info.get("repo_name")
    base_commit = base_info.get("base_commit")
    if not repo_name or not base_commit:
        raise ValueError(f"meta.json missing repo_name/base_commit in {case_dir}")

    maintainer = RepoMaintainer(repo_name=repo_name, base_commit=base_commit)
    repo_dir = maintainer.prepare()

    patch_path = _patch_path_for(case_dir, patch_kind)
    if patch_path is not None and not patch_path.exists():
        raise FileNotFoundError(f"patch file not found: {patch_path}")

    runs_dir = case_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{patch_kind}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    case_tag = _safe_case_tag(case_id)
    if repo_test_path:
        dest_rel = Path(repo_test_path)
    else:
        dest_rel = Path("__llm_tests__") / f"test_{case_tag}.py"

    summary: dict = {
        "case_id": case_id,
        "repo_name": repo_name,
        "base_commit": base_commit,
        "patch_kind": patch_kind,
        "patch_file": str(patch_path) if patch_path else None,
        "test_script_src": str(test_script_src),
        "repo_test_path": dest_rel.as_posix(),
        "started_at": _utc_now(),
        "apply_success": None,
        "test_exit_code": None,
        "duration_seconds": None,
        "status": "started",
        "python_attempts": [],
    }

    worktree_path: Path | None = None
    tmp_root = PROJECT_ROOT / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        worktree_path = Path(
            tempfile.mkdtemp(prefix=f"swe_case_{case_tag}_", dir=str(tmp_root))
        )
        _run(["git", "worktree", "add", "--detach", str(worktree_path), base_commit], cwd=repo_dir)

        primary_base = Path(python_exe).resolve() if python_exe else Path(sys.executable).resolve()
        base_candidates = _build_base_python_candidates(
            primary=primary_base,
            python_candidates=python_candidates,
            auto_python_fallback=auto_python_fallback,
        )
        if not base_candidates:
            raise RuntimeError("No valid python interpreter candidates found.")

        if patch_path is not None:
            apply_cp = _run(
                ["git", "apply", "--whitespace=nowarn", str(patch_path)],
                cwd=worktree_path,
                check=False,
            )
            (run_dir / "git_apply.stdout.txt").write_text(apply_cp.stdout or "", encoding="utf-8")
            (run_dir / "git_apply.stderr.txt").write_text(apply_cp.stderr or "", encoding="utf-8")
            summary["apply_success"] = apply_cp.returncode == 0
            if apply_cp.returncode != 0:
                summary["status"] = "apply_failed"
                return 2
        else:
            summary["apply_success"] = True

        dest_path = worktree_path / dest_rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(test_script_src, dest_path)

        compat_issue_codes = {
            "python_too_new_for_repo_commit",
            "python_too_new_for_legacy_collections_api",
        }
        last_status = "repo_dep_install_failed"

        for i, base_py in enumerate(base_candidates, start=1):
            if managed_venv and python_exe is None:
                venv_root = (PROJECT_ROOT / managed_venv_path).resolve()
                versioned_venv = _venv_path_for_base(str(base_py), venv_root)
                runner_python = _ensure_managed_venv(versioned_venv, base_python=base_py)
            else:
                runner_python = base_py

            attempt_info: dict[str, Any] = {
                "index": i,
                "base_python": str(base_py),
                "runner_python": str(runner_python),
                "version": _python_version(runner_python),
            }
            summary["runner_python"] = str(runner_python)

            if ensure_pytest and not _has_module(runner_python, "pytest", worktree_path):
                pip_pytest_rc = _pip_install(
                    runner_python,
                    worktree_path,
                    ["pytest<8"],
                    run_dir,
                    "pip_pytest",
                )
                summary["pip_pytest_exit_code"] = pip_pytest_rc
                attempt_info["pip_pytest_exit_code"] = pip_pytest_rc
                if pip_pytest_rc != 0:
                    attempt_info["status"] = "pip_pytest_failed"
                    summary["python_attempts"].append(attempt_info)
                    summary["status"] = "pip_pytest_failed"
                    return 2

            if install_repo_deps:
                dep_result = _install_repo_deps_for_commit(
                    python_exe=runner_python,
                    repo_root=worktree_path,
                    run_dir=run_dir,
                    repo_name=repo_name,
                )
                summary["repo_dep_install"] = dep_result
                attempt_info["repo_dep_ok"] = dep_result.get("ok", False)

                if not dep_result.get("ok", False):
                    known_issue = _detect_known_dep_install_issue(run_dir)
                    if known_issue:
                        summary["repo_dep_issue"] = known_issue
                        attempt_info["repo_dep_issue"] = known_issue
                        code = known_issue.get("code")
                        if code in compat_issue_codes and i < len(base_candidates):
                            attempt_info["status"] = "retry_with_other_python"
                            summary["python_attempts"].append(attempt_info)
                            continue
                        if code == "python_too_new_for_repo_commit":
                            summary["status"] = "repo_dep_python_compat_failed"
                        elif code == "numpy_too_new_for_repo_commit":
                            summary["status"] = "repo_dep_numpy_compat_failed"
                        elif code == "requirements_include_path_broken":
                            summary["status"] = "repo_dep_requirements_include_failed"
                        else:
                            summary["status"] = "repo_dep_install_failed"
                    else:
                        summary["status"] = "repo_dep_install_failed"

                    attempt_info["status"] = summary["status"]
                    summary["python_attempts"].append(attempt_info)
                    return 2

                if not dep_result.get("base_install_ok", True):
                    known_issue = _detect_known_dep_install_issue(run_dir)
                    if known_issue:
                        summary["repo_dep_issue"] = known_issue
                        attempt_info["repo_dep_issue"] = known_issue
                        if (
                            known_issue.get("code") in compat_issue_codes
                            and i < len(base_candidates)
                        ):
                            attempt_info["status"] = "retry_with_other_python"
                            summary["python_attempts"].append(attempt_info)
                            continue

                    summary["repo_dep_warning"] = dep_result.get(
                        "base_install_warning",
                        "Base package install failed but execution continues.",
                    )

            if install_editable and not install_repo_deps:
                pip_editable = _run(
                    [str(runner_python), "-m", "pip", "install", "-e", "."],
                    cwd=worktree_path,
                    check=False,
                )
                (run_dir / "pip_editable.stdout.txt").write_text(pip_editable.stdout or "", encoding="utf-8")
                (run_dir / "pip_editable.stderr.txt").write_text(pip_editable.stderr or "", encoding="utf-8")
                summary["pip_editable_exit_code"] = pip_editable.returncode
                attempt_info["pip_editable_exit_code"] = pip_editable.returncode
                if pip_editable.returncode != 0:
                    summary["status"] = "pip_editable_failed"
                    attempt_info["status"] = "pip_editable_failed"
                    summary["python_attempts"].append(attempt_info)
                    return 2

            test_cp = _run_pytest_once(
                runner_python=runner_python,
                worktree_path=worktree_path,
                dest_rel=dest_rel,
                pytest_extra_args=pytest_extra_args,
                run_dir=run_dir,
                log_prefix="pytest",
                disable_cacheprovider=False,
            )
            summary["pytest_cmd"] = test_cp.pytest_cmd  # type: ignore[attr-defined]

            known_pytest_issue = _detect_known_pytest_issue(test_cp.stderr or "")
            if known_pytest_issue:
                summary["pytest_issue"] = known_pytest_issue
                attempt_info["pytest_issue"] = known_pytest_issue

                if known_pytest_issue.get("code") == "pytest_cacheprovider_incompatible":
                    retry_cp = _run_pytest_once(
                        runner_python=runner_python,
                        worktree_path=worktree_path,
                        dest_rel=dest_rel,
                        pytest_extra_args=pytest_extra_args,
                        run_dir=run_dir,
                        log_prefix="pytest_retry_no_cacheprovider",
                        disable_cacheprovider=True,
                    )
                    summary["pytest_retry_cmd"] = retry_cp.pytest_cmd  # type: ignore[attr-defined]
                    retry_issue = _detect_known_pytest_issue(retry_cp.stderr or "")

                    if retry_issue and retry_issue.get("code") in compat_issue_codes and i < len(base_candidates):
                        attempt_info["status"] = "retry_with_other_python"
                        summary["python_attempts"].append(attempt_info)
                        continue

                    summary["test_exit_code"] = retry_cp.returncode
                    summary["status"] = "passed" if retry_cp.returncode == 0 else "failed"
                    attempt_info["test_exit_code"] = retry_cp.returncode
                    attempt_info["status"] = summary["status"]
                    attempt_info["pytest_retry_used"] = True
                    summary["python_attempts"].append(attempt_info)
                    return 0 if retry_cp.returncode == 0 else 1

                if known_pytest_issue.get("code") in compat_issue_codes and i < len(base_candidates):
                    attempt_info["status"] = "retry_with_other_python"
                    summary["python_attempts"].append(attempt_info)
                    continue

            summary["test_exit_code"] = test_cp.returncode
            summary["status"] = "passed" if test_cp.returncode == 0 else "failed"
            attempt_info["test_exit_code"] = test_cp.returncode
            attempt_info["status"] = summary["status"]
            summary["python_attempts"].append(attempt_info)
            return 0 if test_cp.returncode == 0 else 1

        summary["status"] = last_status
        return 2

    finally:
        summary["duration_seconds"] = round(time.perf_counter() - t0, 3)
        summary["finished_at"] = _utc_now()
        _finalize_result_summary(summary)
        output_summary = _ordered_summary_for_output(summary)
        (run_dir / "summary.json").write_text(
            json.dumps(output_summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if worktree_path is not None:
            _run(["git", "worktree", "remove", "--force", str(worktree_path)], cwd=repo_dir, check=False)
            shutil.rmtree(worktree_path, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run one case's generated differentiating test against PR/Model patch."
    )
    p.add_argument("--case", required=True, help="Case ID, e.g. astropy__astropy-12907")
    p.add_argument(
        "--patch",
        choices=["model", "pr", "none"],
        default="model",
        help="Which patch to apply before test run.",
    )
    p.add_argument(
        "--test-script",
        default="test.py",
        help="Test script file name under cases/<case>/, default: test.py",
    )
    p.add_argument(
        "--repo-test-path",
        default=None,
        help="Destination path inside repo worktree for this test file.",
    )
    p.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Extra args appended to pytest command. Example: --pytest-args -k foo -x",
    )
    p.add_argument(
        "--ensure-pytest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ensure pytest is available in selected Python environment.",
    )
    p.add_argument(
        "--managed-venv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use a managed shared venv (recommended).",
    )
    p.add_argument(
        "--managed-venv-path",
        default=".venvs/case_runner",
        help="Managed venv path relative to project root.",
    )
    p.add_argument(
        "--python-exe",
        default=None,
        help="Use this Python executable directly; overrides --managed-venv.",
    )
    p.add_argument(
        "--install-editable",
        action="store_true",
        help="Run standalone 'pip install -e .' only when --no-install-repo-deps is used.",
    )
    p.add_argument(
        "--install-repo-deps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Install per-commit repo dependencies after checkout (recommended).",
    )
    p.add_argument(
        "--auto-python-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto retry with other Python interpreters when compatibility issues are detected.",
    )
    p.add_argument(
        "--python-candidates",
        nargs="*",
        default=[],
        help="Extra python executables to try in fallback chain.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        code = run_case(
            case_id=args.case,
            patch_kind=args.patch,
            test_script_name=args.test_script,
            repo_test_path=args.repo_test_path,
            pytest_extra_args=args.pytest_args,
            ensure_pytest=args.ensure_pytest,
            install_editable=args.install_editable,
            install_repo_deps=args.install_repo_deps,
            managed_venv=args.managed_venv and (args.python_exe is None),
            managed_venv_path=args.managed_venv_path,
            python_exe=args.python_exe,
            auto_python_fallback=args.auto_python_fallback,
            python_candidates=args.python_candidates,
        )
        print(f"[DONE] exit={code}")
        return code
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())