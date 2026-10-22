"""Validate a generated (or hand-finished) harness against CLI-Anything rules.

Checks derived from CONTRIBUTING.md:
  1. required files exist in the standard layout
  2. every .py file compiles
  3. the package imports
  4. every Click command supports --json
  5. unit tests exist and pass (no backend required)
  6. registry-entry.json has all required fields
  7. repl_skin.py is a real copy, not the placeholder
"""

from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_REL = [
    "setup.py",
]
REQUIRED_PKG = [
    "README.md",
    "__init__.py",
    "__main__.py",
    "skills/SKILL.md",
    "tests/__init__.py",
    "tests/test_core.py",
    "utils/repl_skin.py",
]
REGISTRY_FIELDS = [
    "name",
    "display_name",
    "version",
    "description",
    "requires",
    "homepage",
    "source_url",
    "install_cmd",
    "entry_point",
    "skill_md",
    "category",
    "contributors",
]
PLACEHOLDER_MARKER = "unmodified copy of the plugin"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ValidationReport:
    harness_root: Path
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def lines(self) -> list[str]:
        out = []
        for c in self.checks:
            mark = "PASS" if c.ok else "FAIL"
            out.append(f"[{mark}] {c.name}" + (f" - {c.detail}" if c.detail else ""))
        out.append(f"result: {'OK' if self.ok else 'FAILED'}")
        return out


def _find_harness_root(path: Path) -> Path:
    p = path.resolve()
    if p.name == "agent-harness":
        return p
    for child in p.rglob("agent-harness"):
        return child
    return p


def validate(harness_dir: str | Path, python: str | None = None) -> ValidationReport:
    root = _find_harness_root(Path(harness_dir))
    report = ValidationReport(harness_root=root)
    py = python or sys.executable

    for rel in REQUIRED_REL:
        target = root / rel
        report.checks.append(Check(f"file {rel}", target.exists()))

    pkgs = [p for p in root.glob("cli_anything/*") if p.is_dir() and p.name != "__pycache__"]
    if not pkgs:
        report.checks.append(Check("package layout", False, "no cli_anything/<module> found"))
        return report
    pkg = pkgs[0]
    module = pkg.name
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")

    for rel in REQUIRED_PKG:
        target = pkg / rel
        report.checks.append(Check(f"file cli_anything/{module}/{rel}", target.exists()))

    cli_file = pkg / f"{module}_cli.py"
    report.checks.append(Check(f"cli entry {module}_cli.py", cli_file.exists()))

    py_files = sorted(root.rglob("*.py"))
    bad = []
    for f in py_files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{f.name}: {e}")
    report.checks.append(Check("all python compiles", not bad, "; ".join(bad[:3])))

    if cli_file.exists():
        src = cli_file.read_text(encoding="utf-8")
        report.checks.append(
            Check("--json flag present", '"--json"' in src or "'--json'" in src)
        )
        n_cmds = src.count("@main.command()")
        report.checks.append(Check("has commands", n_cmds > 0, f"{n_cmds} commands"))
        try:
            proc = subprocess.run(
                [py, "-c", f"import importlib; importlib.import_module('cli_anything.{module}.{module}_cli')"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=root,
                env=env,
            )
            detail = (proc.stderr or "").strip().splitlines()
            report.checks.append(Check("package imports", proc.returncode == 0, detail[-1] if detail else ""))
        except (OSError, subprocess.TimeoutExpired) as e:
            report.checks.append(Check("package imports", False, str(e)))

    skin = pkg / "utils" / "repl_skin.py"
    if skin.exists():
        text = skin.read_text(encoding="utf-8")
        report.checks.append(
            Check(
                "repl_skin is a real copy",
                PLACEHOLDER_MARKER not in text,
                "still the placeholder - copy from the plugin" if PLACEHOLDER_MARKER in text else "",
            )
        )

    reg = root / "registry-entry.json"
    if reg.exists():
        try:
            entry = json.loads(reg.read_text(encoding="utf-8"))
            missing = [f for f in REGISTRY_FIELDS if f not in entry]
            report.checks.append(
                Check("registry-entry fields", not missing, f"missing: {missing}" if missing else "")
            )
        except json.JSONDecodeError as e:
            report.checks.append(Check("registry-entry fields", False, str(e)))
    else:
        report.checks.append(Check("registry-entry fields", False, "registry-entry.json missing"))

    test_file = pkg / "tests" / "test_core.py"
    if test_file.exists():
        try:
            with tempfile.TemporaryDirectory() as td:
                proc = subprocess.run(
                    [py, "-m", "pytest", str(test_file), "-q", "--no-header", "-p", "no:cacheprovider"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=td,
                    env=env,
                )
        except (OSError, subprocess.TimeoutExpired) as e:
            report.checks.append(Check("unit tests pass", False, str(e)))
            return report
        tail = (proc.stdout or "").strip().splitlines()
        last = tail[-1] if tail else (proc.stderr or "")[:200]
        report.checks.append(Check("unit tests pass", proc.returncode == 0, last))

    return report
