"""
Package management utilities for AI agents.

This module provides functions to install and manage Python packages
in kernel environments, ensuring agents can work with required dependencies.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _run_json(session, code: str, *, timeout: float) -> dict[str, Any]:
    """
    Execute `code` in the kernel and return a JSON object parsed from the LAST
    line printed to stdout. If the code does not produce valid JSON on its last
    stdout line, raise RuntimeError. This is strict by design.
    """
    from .execution import execute_code

    result = await execute_code(session, code, timeout=timeout)

    if result.status != "ok":
        raise RuntimeError(f"Kernel error: {result.error_message or 'unknown'}")

    stdout = result.stdout or ""
    if not stdout.strip():
        raise RuntimeError("Kernel produced no stdout to parse as JSON.")

    last = stdout.strip().splitlines()[-1]
    try:
        return json.loads(last)
    except Exception as e:
        # include a small excerpt for diagnostics
        excerpt = stdout[-500:] if len(stdout) > 500 else stdout
        raise RuntimeError(
            f"Failed to parse JSON from kernel stdout: {e}\n"
            f"Last line: {last!r}\n"
            f"Stdout excerpt:\n{excerpt}"
        ) from e


async def check_package_availability(
    session, packages: list[str], timeout: float = 120.0
) -> dict[str, bool]:
    """
    Strictly check which pip packages are *installed* in the kernel environment.
    Uses importlib.metadata (distribution presence), not module import names.

    Args:
        session: Kernel session object
        packages: pip distribution names to verify (e.g., "beautifulsoup4", "plotly")
        timeout: seconds to wait for kernel execution

    Returns:
        { "pkg": True|False, ... }

    Raises:
        RuntimeError if kernel did not produce valid JSON as the last stdout line.
    """
    # Strip extras generically inside the kernel and query metadata.
    # No alias maps; we check exactly the distributions you requested.
    code = f"""
import importlib.metadata as _im
import json, re

PKGS = {packages!r}

def base_name(name: str) -> str:
    # strip extras (e.g., 'pkg[extra1,extra2]' -> 'pkg')
    i = name.find('[')
    return name if i < 0 else name[:i]

status = {{}}
for p in PKGS:
    dist = base_name(p)
    try:
        _ = _im.version(dist)
        status[p] = True
    except _im.PackageNotFoundError:
        status[p] = False

print(json.dumps(status))
"""
    return await _run_json(session, code, timeout=timeout)


async def ensure_packages_with_report(
    session, packages: list[str], timeout: float = 120.0
) -> dict[str, Any]:
    """
    Install only the missing pip packages and return a strict, per-package report.

    Returns:
        {
          "success": bool,
          "report": {
            "<pip>": {
              "pip": "<pip>",
              "already": bool,         # installed before
              "installed": bool,       # installed during this call
              "success": bool,         # final state is installed
              "error": str|None,       # textual reason on failure
              "pip_returncode": int|None,
              "pip_stderr": str
            }, ...
          }
        }

    Raises:
        RuntimeError if kernel did not produce valid JSON as the last stdout line.
    """
    code = f"""
import sys, subprocess, json, shutil
import importlib.metadata as _im

PKGS = {packages!r}

def base_name(name: str) -> str:
    i = name.find('[')
    return name if i < 0 else name[:i]

def is_installed(pip_name: str) -> bool:
    dist = base_name(pip_name)
    try:
        _im.version(dist)
        return True
    except _im.PackageNotFoundError:
        return False

def _has_pip() -> bool:
    try:
        import pip  # noqa: F401
        return True
    except ImportError:
        return False

def _install_cmd(pip_name: str) -> list[str]:
    # Prefer uv if available (faster, works in uv-managed venvs without pip)
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", pip_name, "--quiet"]
    # Fall back to python -m pip
    return [sys.executable, "-m", "pip", "install", pip_name,
            "--no-warn-script-location", "--quiet"]

rep = {{}}
for pip_name in PKGS:
    entry = {{
        "pip": pip_name,
        "already": False,
        "installed": False,
        "success": False,
        "error": None,
        "pip_returncode": None,
        "pip_stderr": "",
    }}

    if is_installed(pip_name):
        entry["already"] = True
        entry["success"] = True
        rep[pip_name] = entry
        continue

    # Install the package
    try:
        cmd = _install_cmd(pip_name)
        res = subprocess.run(cmd, capture_output=True, text=True)
        entry["pip_returncode"] = res.returncode
        entry["pip_stderr"] = res.stderr or ""
        if res.returncode == 0 and is_installed(pip_name):
            entry["installed"] = True
            entry["success"] = True
        else:
            entry["error"] = f"install exit code {{res.returncode}}"
    except Exception as e:
        entry["error"] = str(e)

    rep[pip_name] = entry

print(json.dumps({{"success": all(v["success"] for v in rep.values()), "report": rep}}))
"""
    return await _run_json(session, code, timeout=timeout)


async def ensure_packages(session, packages: list[str], timeout: float = 120.0) -> bool:
    """
    Back-compat wrapper: install and return overall success as bool.
    Raises if kernel JSON is malformed.
    """
    rep = await ensure_packages_with_report(session, packages, timeout=timeout)
    return bool(rep.get("success", False))


async def install_package(session, package: str, timeout: float = 60.0) -> bool:
    """Install a single pip package and return success as bool."""
    rep = await ensure_packages_with_report(session, [package], timeout=timeout)
    return bool(rep.get("success", False))


async def update_dependencies(session, packages: list[str], timeout: float = 120.0) -> bool:
    """
    Check for required dependencies (by distribution presence) and install any missing.
    Strict: no best-effort parsing. Returns True only if everything ends up installed.
    """
    availability = await check_package_availability(session, packages, timeout=timeout)
    missing = [p for p, ok in availability.items() if not ok]

    if not missing:
        logger.info("✅ All %d packages already available: %s", len(packages), packages)
        return True

    logger.info("📦 %d packages need installation: %s", len(missing), missing)
    report = await ensure_packages_with_report(session, missing, timeout=timeout)

    ok_all = bool(report.get("success", False))
    detail = report.get("report", {})

    for pip_name in missing:
        entry = detail.get(pip_name, {})
        if entry.get("success"):
            if entry.get("already"):
                logger.info("✅ %s already available", pip_name)
            elif entry.get("installed"):
                logger.info("✅ %s installed", pip_name)
        else:
            logger.warning(
                "❌ %s failed: %s",
                pip_name,
                entry.get("error") or entry.get("pip_stderr") or "unknown",
            )

    return ok_all


# Common package sets for convenience (unchanged)
SCIENTIFIC_PACKAGES = ["numpy", "scipy", "matplotlib", "pandas"]
ML_PACKAGES = ["scikit-learn", "tensorflow", "torch", "transformers"]
DATA_VIZ_PACKAGES = ["matplotlib", "seaborn", "plotly", "bokeh"]
WEB_PACKAGES = ["requests", "aiohttp", "fastapi", "flask"]
