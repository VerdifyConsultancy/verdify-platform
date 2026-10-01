"""Restore only our exact prior generated diagnostic bytes before ordinary patch."""

import runpy
from pathlib import Path

if "Import" in globals():
    Import("env")  # noqa: F821 — PlatformIO/SCons supplied
    project = Path(env.subst("$PROJECT_DIR"))  # noqa: F821
    hooks = runpy.run_path(str(project.resolve().parents[2] / "build_api_stall_trace.py"))
    hooks["reset"](project)
