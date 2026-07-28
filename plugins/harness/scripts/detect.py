#!/usr/bin/env python3
"""Work out how to check code in whatever repository we happen to be in.

The harness has to run in every repo the user opens, so nothing here may assume
a stack. Detection produces a profile: a list of checks, each tagged with the
scope it runs at (one file, or the whole project), whether a failure should
block, and whether its tool is actually installed.

Detection follows a strict order of authority:

1. **What the repo declares.** `package.json` scripts are the repo's own
   statement of how it is checked. This beats guessing, and it is how
   `oxlint`/`oxfmt` get picked up without this file having heard of them. It is
   also code the repo controls, so it is marked `source: "repo"` and withheld
   until the user trusts this repository.
2. **Per-file tools the repo has opted into.** A tool counts as opted-in only
   when it is installed project-locally or has a config file in the repo. A
   globally installed ruff must never gate a repo that does not use ruff.
3. **Universal syntax checks.** `py_compile`, `node --check`, `bash -n`,
   `json.tool` need no configuration, so even a repo with zero tooling is
   protected against broken edits.

Formatting never blocks. It is taste, and a turn spent re-indenting is a turn
wasted. Syntax, lint and type failures block, but only after `runner.py`
confirms the edit introduced them.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from state import profiles_dir, read_json, repo_key, write_json

# Ordered by speed and certainty. The per-edit gate runs FAST_KINDS on the one
# touched file; the end-of-turn gate runs HEAVY_KINDS across the project.
FAST_KINDS = ("syntax", "format", "lint", "typecheck")
HEAVY_KINDS = ("typecheck", "lint", "test", "build")

PROFILE_VERSION = 7

# Presence or content of these decides the profile, and changes to any of them
# invalidate the cache.
MANIFESTS = (
    "package.json",
    "tsconfig.json",
    "pyproject.toml",
    "requirements.txt",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "go.mod",
    "Cargo.toml",
    "Makefile",
    "composer.json",
    "Gemfile",
    "pom.xml",
    "deno.json",
    ".harness.json",
)

TS_EXT = (".ts", ".tsx", ".mts", ".cts")
JS_EXT = (".js", ".jsx", ".mjs", ".cjs")
WEB_EXT = TS_EXT + JS_EXT
PY_EXT = (".py", ".pyi")


def _check(
    kind: str,
    argv: list[str],
    *,
    scope: str,
    label: str,
    blocking: bool = True,
    extensions: tuple[str, ...] = (),
    source: str = "plugin",
) -> dict[str, Any]:
    """One check. `source` records who wrote the `argv`, and it is load-bearing.

    A check whose command this file composed is safe to run in any repo: only
    the file path comes from outside. A check whose command came from the repo
    is arbitrary code that arrived with a clone, and it does not run until the
    user has said so. Nothing downstream can tell those apart without this.
    """
    return {
        "kind": kind,
        "argv": argv,
        "scope": scope,
        "label": label,
        "blocking": blocking,
        "extensions": list(extensions),
        "source": source,
    }


# ------------------------------------------------------------ tool resolution


def _local_bin(root: Path, name: str) -> str | None:
    """Prefer a binary vendored in the project over anything on PATH.

    A repo pinning eslint 8 must not be checked by a global eslint 9. Version
    mismatches produce failures that are the harness's fault, and those are the
    failures that get a harness uninstalled.
    """
    for path in (
        root / "node_modules" / ".bin" / name,
        root / ".venv" / "bin" / name,
        root / "venv" / "bin" / name,
    ):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _opted_in(root: Path, name: str, configs: tuple[str, ...]) -> str | None:
    """Resolve a per-file tool only if this repo actually uses it.

    Project-local installation counts as opt-in on its own. A global binary
    counts only when the repo carries config for it — otherwise a tool the user
    happens to have installed would start gating repos that never asked for it.
    """
    if local := _local_bin(root, name):
        return local
    globally = shutil.which(name)
    if not globally:
        return None
    for config in configs:
        if (root / config).exists():
            return globally
    # A tool named in pyproject.toml/setup.cfg counts too, e.g. [tool.ruff].
    for manifest in ("pyproject.toml", "setup.cfg", "tox.ini"):
        path = root / manifest
        if path.is_file():
            try:
                if f"tool.{name}" in path.read_text(encoding="utf-8", errors="replace"):
                    return globally
            except OSError:
                continue
    return None


# ------------------------------------------------------- what the repo says


def _package_runner(root: Path) -> list[str] | None:
    """The package manager this repo uses, decided by its lockfile."""
    for lockfile, manager in (
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
    ):
        if (root / lockfile).is_file() and (found := shutil.which(manager)):
            return [found]
    return [npm] if (npm := shutil.which("npm")) else None


# Script name -> (kind, blocking). Scripts whose names say "fix" or "watch" are
# skipped: a hook must never rewrite files or start a long-running process.
SCRIPT_KINDS = {
    "lint": ("lint", True),
    "typecheck": ("typecheck", True),
    "type-check": ("typecheck", True),
    "tsc": ("typecheck", True),
    "test": ("test", True),
    "test:unit": ("test", True),
    "build": ("build", True),
    "format": ("format", False),
    "fmt": ("format", False),
}


def _declared_script_checks(root: Path, pkg: dict[str, Any]) -> list[dict[str, Any]]:
    scripts = pkg.get("scripts")
    if not isinstance(scripts, dict):
        return []
    runner = _package_runner(root)
    if not runner:
        return []
    checks: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for name, (kind, blocking) in SCRIPT_KINDS.items():
        if name not in scripts or kind in claimed:
            continue
        claimed.add(kind)
        checks.append(
            _check(
                kind,
                [*runner, "run", "--silent", name],
                scope="project",
                label=f"{Path(runner[0]).name} run {name}",
                blocking=blocking,
                source="repo",
            )
        )
    return checks


# --------------------------------------------------------------- ecosystems


def _node_checks(root: Path) -> list[dict[str, Any]]:
    """Per-file JS/TS checks. Project-wide ones come from declared scripts."""
    checks: list[dict[str, Any]] = []

    if node := shutil.which("node"):
        # Parse-only: no config, no project graph, works in any JS repo.
        checks.append(
            _check(
                "syntax",
                [node, "--check", "{file}"],
                scope="file",
                label="node --check",
                extensions=JS_EXT,
            )
        )

    if biome := _opted_in(root, "biome", ("biome.json", "biome.jsonc")):
        checks.append(
            _check(
                "lint",
                [biome, "check", "--no-errors-on-unmatched", "{file}"],
                scope="file",
                label="biome check",
                extensions=WEB_EXT,
            )
        )
        return checks

    if oxlint := _opted_in(root, "oxlint", (".oxlintrc.json", "oxlint.json")):
        checks.append(
            _check(
                "lint",
                [oxlint, "{file}"],
                scope="file",
                label="oxlint",
                extensions=WEB_EXT,
            )
        )
    elif eslint := _opted_in(
        root, "eslint", ("eslint.config.js", "eslint.config.mjs", ".eslintrc", ".eslintrc.json", ".eslintrc.cjs")
    ):
        checks.append(
            _check(
                "lint",
                [eslint, "--no-warn-ignored", "--max-warnings", "0", "{file}"],
                scope="file",
                label="eslint",
                extensions=WEB_EXT,
            )
        )

    if prettier := _opted_in(root, "prettier", (".prettierrc", ".prettierrc.json", "prettier.config.js")):
        checks.append(
            _check(
                "format",
                [prettier, "--check", "--no-color", "{file}"],
                scope="file",
                label="prettier",
                blocking=False,
                extensions=WEB_EXT + (".json", ".css", ".scss", ".md"),
            )
        )
    return checks


def _node_project_checks(root: Path) -> list[dict[str, Any]]:
    """tsc needs the whole project graph, so it can only run project-wide."""
    if not (root / "tsconfig.json").is_file():
        return []
    if tsc := _local_bin(root, "tsc") or shutil.which("tsc"):
        return [_check("typecheck", [tsc, "--noEmit"], scope="project", label="tsc --noEmit")]
    return []


def _python_checks(root: Path) -> list[dict[str, Any]]:
    checks = [
        _check(
            "syntax",
            [sys.executable, "-m", "py_compile", "{file}"],
            scope="file",
            label="py_compile",
            extensions=PY_EXT,
        )
    ]

    if ruff := _opted_in(root, "ruff", ("ruff.toml", ".ruff.toml")):
        checks.append(
            _check("lint", [ruff, "check", "--quiet", "{file}"], scope="file", label="ruff check", extensions=PY_EXT)
        )
        checks.append(
            _check(
                "format",
                [ruff, "format", "--check", "--quiet", "{file}"],
                scope="file",
                label="ruff format",
                blocking=False,
                extensions=PY_EXT,
            )
        )
    else:
        if flake8 := _opted_in(root, "flake8", (".flake8",)):
            checks.append(
                _check("lint", [flake8, "{file}"], scope="file", label="flake8", extensions=PY_EXT)
            )
        if black := _opted_in(root, "black", ()):
            checks.append(
                _check(
                    "format",
                    [black, "--check", "--quiet", "{file}"],
                    scope="file",
                    label="black",
                    blocking=False,
                    extensions=PY_EXT,
                )
            )

    if mypy := _opted_in(root, "mypy", ("mypy.ini", ".mypy.ini")):
        checks.append(
            _check("typecheck", [mypy, "{file}"], scope="file", label="mypy", extensions=PY_EXT)
        )
    elif pyright := _opted_in(root, "pyright", ("pyrightconfig.json",)):
        checks.append(
            _check("typecheck", [pyright, "{file}"], scope="file", label="pyright", extensions=PY_EXT)
        )

    if _has_pytest(root) and (pytest := _local_bin(root, "pytest") or shutil.which("pytest")):
        checks.append(_check("test", [pytest, "-q", "-x"], scope="project", label="pytest"))
    return checks


def _has_pytest(root: Path) -> bool:
    """Whether this repo runs its tests with pytest.

    A dedicated pytest config settles it. Otherwise a tests directory only
    counts when something in the repo also declares the dependency, since a
    `tests/` folder alone says nothing about the runner.
    """
    if (root / "pytest.ini").is_file():
        return True
    if not any((root / d).is_dir() for d in ("tests", "test")):
        return False
    declarations = ["pyproject.toml", "setup.cfg", "tox.ini", *(p.name for p in root.glob("requirements*.txt"))]
    for name in declarations:
        path = root / name
        if path.is_file():
            try:
                if "pytest" in path.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                continue
    return False


def _go_checks() -> list[dict[str, Any]]:
    go = shutil.which("go")
    if not go:
        return []
    checks = [
        _check("build", [go, "build", "./..."], scope="project", label="go build"),
        _check("lint", [go, "vet", "./..."], scope="project", label="go vet"),
        _check("test", [go, "test", "./..."], scope="project", label="go test"),
    ]
    if gofmt := shutil.which("gofmt"):
        checks.insert(
            0,
            _check(
                "format",
                [gofmt, "-l", "{file}"],
                scope="file",
                label="gofmt",
                blocking=False,
                extensions=(".go",),
            ),
        )
    return checks


def _rust_checks() -> list[dict[str, Any]]:
    cargo = shutil.which("cargo")
    if not cargo:
        return []
    return [
        _check("format", [cargo, "fmt", "--check"], scope="project", label="cargo fmt", blocking=False),
        _check("typecheck", [cargo, "check", "--quiet"], scope="project", label="cargo check"),
        _check("test", [cargo, "test", "--quiet"], scope="project", label="cargo test"),
    ]


def _universal_checks() -> list[dict[str, Any]]:
    """Zero-config syntax checks that work in any repository."""
    checks = [
        _check(
            "syntax",
            [sys.executable, "-m", "json.tool", "{file}", os.devnull],
            scope="file",
            label="json syntax",
            extensions=(".json",),
        )
    ]
    if bash := shutil.which("bash"):
        checks.append(
            _check(
                "syntax",
                [bash, "-n", "{file}"],
                scope="file",
                label="bash syntax",
                extensions=(".sh", ".bash"),
            )
        )
    return checks


# ------------------------------------------------------------------ profile


def _fingerprint(root: Path) -> str:
    parts = []
    for name in MANIFESTS:
        try:
            stat = (root / name).stat()
            parts.append(f"{name}:{int(stat.st_mtime)}:{stat.st_size}")
        except OSError:
            continue
    return "|".join(parts)


def _dedupe(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a project check when a per-file check of the same kind exists.

    Running the whole test suite per file would be absurd, and running project
    lint when we already lint the touched file just re-reports the rest of the
    repo's pre-existing problems.
    """
    per_file_kinds = {c["kind"] for c in checks if c["scope"] == "file"}
    out = []
    for check in checks:
        if check["scope"] == "project" and check["kind"] in per_file_kinds and check["kind"] != "test":
            continue
        out.append(check)
    return out


def build_profile(root: Path) -> dict[str, Any]:
    languages: list[str] = []
    checks: list[dict[str, Any]] = []

    pkg = read_json(root / "package.json", default={}) or {}
    if (root / "package.json").is_file():
        languages.append("typescript" if (root / "tsconfig.json").is_file() else "javascript")
        checks += _node_checks(root)
        checks += _declared_script_checks(root, pkg)
        checks += _node_project_checks(root)

    if any(
        (root / n).is_file() for n in ("pyproject.toml", "requirements.txt", "pytest.ini", "setup.py")
    ):
        languages.append("python")
        checks += _python_checks(root)

    if (root / "go.mod").is_file():
        languages.append("go")
        checks += _go_checks()

    if (root / "Cargo.toml").is_file():
        languages.append("rust")
        checks += _rust_checks()

    checks += _universal_checks()

    profile: dict[str, Any] = {
        "version": PROFILE_VERSION,
        "repo_root": str(root),
        "fingerprint": _fingerprint(root),
        "languages": languages or ["unknown"],
        "is_git": (root / ".git").exists(),
        "checks": _dedupe(checks),
    }
    _apply_overrides(root, profile)
    _mark_vendored(root, profile)
    return profile


def _mark_vendored(root: Path, profile: dict[str, Any]) -> None:
    """A tool resolved out of the repo is the repo's code, whatever composed the argv.

    `_local_bin` deliberately prefers `node_modules/.bin/` and `.venv/bin/` so a
    repo is checked by the version it pins. That preference is right, and it also
    means a file that arrived with a clone gets executed — so these are marked
    repo-authored on the same footing as a `.harness.json` entry.
    """
    inside = str(root.resolve())
    for check in profile["checks"]:
        argv = check.get("argv") or []
        if not argv:
            continue
        try:
            if str(Path(argv[0]).resolve()).startswith(inside + "/"):
                check["source"] = "repo"
        except (OSError, ValueError):
            continue


def _apply_overrides(root: Path, profile: dict[str, Any]) -> None:
    """Let a repo correct the guess via `.harness.json`.

    Supported keys: `disable` (check kinds to drop) and `checks` (extra check
    definitions appended verbatim). Deliberately tiny — an escape hatch, not a
    configuration language.
    """
    override = read_json(root / ".harness.json", default=None)
    if not isinstance(override, dict):
        return
    if isinstance(disabled := override.get("disable"), list):
        profile["checks"] = [c for c in profile["checks"] if c.get("kind") not in disabled]
        profile["disabled_kinds"] = disabled
    if isinstance(extra := override.get("checks"), list):
        # Verbatim except for provenance, which the repo does not get to claim.
        profile["checks"].extend({**c, "source": "repo"} for c in extra if isinstance(c, dict))
    profile["has_override"] = True


def _withhold_untrusted(root: Path, profile: dict[str, Any]) -> dict[str, Any]:
    """Move repo-authored commands out of `checks` until this repo is trusted.

    Applied on every read rather than baked into the cache, because trust is
    granted between runs and a cached profile would keep withholding after the
    user had said yes.
    """
    import trust

    if trust.is_trusted(root, profile):
        profile["withheld_checks"] = []
        return profile

    withheld = trust.repo_authored(profile)
    profile["checks"] = [c for c in profile["checks"] if c.get("source") != "repo"]
    profile["withheld_checks"] = withheld
    return profile


def get_profile(root: Path, *, refresh: bool = False) -> dict[str, Any]:
    """Load the cached profile, rebuilding when the repo's manifests changed."""
    cache = profiles_dir() / f"{repo_key(root)}.json"
    if not refresh:
        cached = read_json(cache, default=None)
        if (
            isinstance(cached, dict)
            and cached.get("version") == PROFILE_VERSION
            and cached.get("fingerprint") == _fingerprint(root)
        ):
            return _withhold_untrusted(root, cached)
    profile = build_profile(root)
    try:
        write_json(cache, profile)
    except OSError:
        pass  # A read-only data dir degrades to rebuilding, not to failing.
    return _withhold_untrusted(root, profile)


def checks_for_file(profile: dict[str, Any], file_path: str) -> list[dict[str, Any]]:
    suffix = Path(file_path).suffix.lower()
    out = []
    for check in profile.get("checks", []):
        if check.get("scope") != "file" or check.get("kind") not in FAST_KINDS:
            continue
        extensions = check.get("extensions") or []
        if extensions and suffix not in extensions:
            continue
        out.append(check)
    return out


def heavy_checks(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        c
        for c in profile.get("checks", [])
        if c.get("scope") == "project" and c.get("kind") in HEAVY_KINDS
    ]


if __name__ == "__main__":
    from state import repo_root

    target = repo_root(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(get_profile(target, refresh=True), indent=2))
