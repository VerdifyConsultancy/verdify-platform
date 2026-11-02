"""The irrigation writes: run and stop, API-IRR-06 and API-IRR-07 (#1177), and the rest of API-IRR-03 to -13 (#1156).

A stub until #1177 fills it. ``api/operator_api.py`` (#1148) already includes ``router`` under ``/v1`` with the
bearer, session and role checks (every method but GET and HEAD for operators only), so this module carries no auth
of its own (``operator_routes/__init__.py`` has the rules).
"""

from fastapi import APIRouter

router = APIRouter()
