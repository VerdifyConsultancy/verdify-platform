"""The irrigation reads: API-IRR-01, API-IRR-02, API-IRR-10 and API-IRR-11 (#1155).

A stub until #1155 fills it. ``api/operator_api.py`` (#1148) already includes ``router`` under ``/v1`` with the
bearer, session and role checks, so this module carries no auth of its own (``operator_routes/__init__.py`` has the
rules).
"""

from fastapi import APIRouter

router = APIRouter()
