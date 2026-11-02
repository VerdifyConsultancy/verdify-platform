"""Route modules of the operator API (architecture §6 and §8).

Rules every module here follows:

- One ``router = APIRouter()`` per module (``reads``, ``series``, ``writes``,
  ``irrigation_reads``, ``irrigation_writes``). Paths are written without the
  ``/v1`` prefix.
- Every route sets ``operation_id`` to its design id in
  docs/design/dashboard/openapi/dashboard-api.v1.yaml, and its response follows
  the committed schema.
- No auth dependencies: the operator API (``api/operator_api.py``, #1148)
  includes each module under ``/v1`` with the bearer, session and role checks.
- No SQL: a route only calls ``verdify_services``.
- Whichever PR lands first creates a module's stub.
"""
