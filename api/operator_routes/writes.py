"""The operator API's write routes: settings and alerts, API-10, API-11, API-15 and API-16 (#1150).

A stub until #1150 fills it. ``api/operator_api.py`` (#1148) already includes ``router`` under ``/v1`` with the
bearer, session and role checks (every method but GET and HEAD for operators only), so this module carries no auth
of its own (``operator_routes/__init__.py`` has the rules).
"""

from fastapi import APIRouter

router = APIRouter()
