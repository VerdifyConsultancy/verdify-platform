"""Operator API route modules (architecture §6 and §8; execution plan §2.1).

The rules every module here follows:

- One ``router = APIRouter()`` per module: ``reads``, ``series``, ``writes``, ``irrigation_reads`` and
  ``irrigation_writes``. Whichever PR lands first creates a module's stub.
- Paths are written without the ``/v1`` prefix; ``api/operator_api.py`` (#1148) includes each router under
  ``/v1``.
- Every route sets ``operation_id`` to its design id (``API-09``, ``API-IRR-06``, ...), the canonical
  ``operationId`` in docs/design/dashboard/openapi/dashboard-api.v1.yaml.
- No auth dependencies: #1148 adds the session and role checks when it includes the router, and builds the
  ``verdify_services.actor.Actor`` from the session. A route never reads an actor, source or ``*_by`` value
  from the request.
- No SQL: a route only calls ``verdify_services``. A ``ServiceError`` propagates, and the operator API renders
  it as ``application/problem+json`` (``problem.body()`` with ``problem.status``). A write passes the
  ``Idempotency-Key`` header as ``request_id`` and returns ``WriteResult.rest_headers()`` with its body.
"""
