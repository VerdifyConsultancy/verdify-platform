"""Qualify the token boundary without database, email or provider traffic."""

import ast
import json
import urllib
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"success": True, "hostname": "lab.verdify.ai", "action": "contact"}, True),
        ({"success": False, "hostname": "lab.verdify.ai", "action": "contact"}, False),
        ({"success": True, "hostname": "other.example", "action": "contact"}, False),
        ({"success": True, "hostname": "lab.verdify.ai", "action": "login"}, False),
        ({"success": True}, False),
    ],
)
def test_siteverify_requires_success_hostname_and_action(result, expected):
    source = ast.parse(Path("api/main.py").read_text())
    function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "_turnstile_verify_sync")
    namespace = {"urllib": urllib, "json": json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<turnstile>", "exec"), namespace)  # noqa: S102 — fixed repository function, no external code
    with patch("urllib.request.urlopen") as opened:
        opened.return_value.__enter__.return_value.read.return_value = json.dumps(result).encode()
        assert namespace["_turnstile_verify_sync"]("fixture-secret", "fixture-token", "192.0.2.1") is expected
