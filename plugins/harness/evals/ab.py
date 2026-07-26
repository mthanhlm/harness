#!/usr/bin/env python3
"""Measure whether the harness actually improves the output, and at what cost.

Runs the same task twice — once with the plugin loaded, once without — against a
fresh copy of a fixture repository each time, then grades the result with tests
the model never saw. Reports pass rate and dollars per arm.

This exists because the claim the plugin is built on is falsifiable and should be
tested rather than asserted: that a cheaper model with a contract and working
checks beats an expensive model without them. Run it on two models to find out:

    python3 ab.py --model claude-sonnet-5 --runs 3
    python3 ab.py --model claude-opus-5   --runs 3

If the harness arm on the cheaper model matches or beats the bare arm on the
dearer one, the default model can change. If it does not, that is the answer.

`claude plugin eval` does the same job with more machinery, but it is gated to
early access; this needs nothing beyond the CLI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def prepare(case: Path, workdir: Path) -> Path:
    """A fresh checkout per run, so no arm inherits another's state."""
    repo = workdir / "repo"
    shutil.copytree(case / "fixture", repo)
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "eval@local"],
        ["git", "config", "user.name", "eval"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "fixture"],
    ):
        _run(argv, repo, 60)
    return repo


def grade(case: Path, repo: Path) -> tuple[bool, str]:
    """Run hidden tests the model never saw.

    Grading against tests the model wrote would measure whether it can agree
    with itself. These are copied in after the run, so they measure behaviour.
    """
    graded = repo / "test_graded.py"
    shutil.copyfile(case / "grade" / "test_graded.py", graded)
    code, output = _run([sys.executable, "-m", "pytest", "-q", str(graded)], repo, 180)
    return code == 0, output.strip()[-600:]


def run_once(case: Path, model: str, with_plugin: bool, timeout: int) -> dict:
    task = (case / "task.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="harness-eval-") as tmp:
        repo = prepare(case, Path(tmp))
        argv = [
            "claude",
            "--model", model,
            "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
            "--output-format", "json",
            "-p", task,
        ]
        if with_plugin:
            argv[1:1] = ["--plugin-dir", str(PLUGIN_ROOT)]

        code, output = _run(argv, repo, timeout)
        cost, turns = 0.0, 0
        try:
            payload = json.loads(output)
            cost = float(payload.get("total_cost_usd") or 0)
            turns = int(payload.get("num_turns") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        if code == 124:
            return {"passed": False, "cost": cost, "turns": turns, "note": "agent timed out"}
        passed, detail = grade(case, repo)
        return {"passed": passed, "cost": cost, "turns": turns, "note": "" if passed else detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--case", default="slugify")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    case = HERE / "cases" / args.case
    if not (case / "task.md").is_file():
        print(f"No such case: {case}", file=sys.stderr)
        return 1

    print(f"case={args.case}  model={args.model}  runs={args.runs}\n")
    summary = {}
    for label, with_plugin in (("harness", True), ("bare", False)):
        results = []
        for index in range(args.runs):
            result = run_once(case, args.model, with_plugin, args.timeout)
            results.append(result)
            mark = "pass" if result["passed"] else "FAIL"
            note = f"  {result['note'].splitlines()[0][:70]}" if result["note"] else ""
            print(f"  {label:8} run {index + 1}: {mark}  ${result['cost']:.3f}  {result['turns']} turns{note}")
        summary[label] = results
        print()

    print("summary")
    for label, results in summary.items():
        passed = sum(1 for r in results if r["passed"])
        costs = [r["cost"] for r in results]
        print(
            f"  {label:8} {passed}/{len(results)} passed   "
            f"mean ${statistics.mean(costs):.3f}   total ${sum(costs):.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
