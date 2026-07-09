"""Credential-hygiene guards for standalone database renderer scripts."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_SCRIPTS = (
    "scripts/daily-summary-snapshot.py",
    "scripts/render-equipment-page.py",
    "scripts/render-zone-pages.py",
    "scripts/render-crop-profiles.py",
    "scripts/vault-operations-writer.py",
)


def _load_script(relative_path: str) -> ModuleType:
    module_name = "credential_hygiene_" + Path(relative_path).stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dsn_helper(relative_path: str):
    module = _load_script(relative_path)
    return module.get_db_url if relative_path.endswith("daily-summary-snapshot.py") else module._database_dsn


@pytest.mark.parametrize("relative_path", DB_SCRIPTS)
def test_database_password_has_no_literal_fallback(relative_path: str) -> None:
    """POSTGRES_PASSWORD must be required, never defaulted from source."""

    tree = ast.parse((ROOT / relative_path).read_text(), filename=relative_path)
    password_lookups = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        key = node.args[0]
        if isinstance(key, ast.Constant) and key.value == "POSTGRES_PASSWORD":
            password_lookups.append(node)

    assert password_lookups, f"{relative_path} must read POSTGRES_PASSWORD"
    assert all(len(call.args) == 1 for call in password_lookups), (
        f"{relative_path} must not provide a source-code password fallback"
    )


@pytest.mark.parametrize("relative_path", DB_SCRIPTS)
def test_database_dsn_prefers_explicit_injection(relative_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = "postgresql://unit-test.invalid/verdify"
    monkeypatch.setenv("VERDIFY_DSN", expected)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    assert _dsn_helper(relative_path)() == expected


@pytest.mark.parametrize("relative_path", DB_SCRIPTS)
def test_database_password_injection_constructs_local_dsn(
    relative_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = "unit-" + "test-value"
    monkeypatch.delenv("VERDIFY_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", password)

    assert _dsn_helper(relative_path)() == (
        f"postgresql://verdify:{password}@127.0.0.1:5432/verdify"
    )


@pytest.mark.parametrize("relative_path", DB_SCRIPTS)
def test_database_authentication_fails_closed(relative_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERDIFY_DSN", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="VERDIFY_DSN or POSTGRES_PASSWORD is required"):
        _dsn_helper(relative_path)()
