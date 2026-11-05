"""Operator API route modules (architecture §6 and §8; execution plan §2.1).

The rules every module here follows:

- One ``router = APIRouter()`` per module: ``reads``, ``series``, ``writes``, ``irrigation_reads`` and
  ``irrigation_writes``. #1148 created the ``irrigation_reads`` and ``irrigation_writes`` stubs; ``series`` is
  #1169's to create, and the operator API includes it as soon as its file exists. ``setpoint_server`` is no route
  module: it is API-20's one HTTP call, which ``verdify_services`` may not make (#1170). Nor is ``light_resync``: the
  Back to automatic resync the Equipment page's API-10 write starts, and the per-light lock it shares with API-20
  (#1300).
- Paths are written without the ``/v1`` prefix; ``api/operator_api.py`` (#1148) includes each router under
  ``/v1`` with ``include_v1()``.
- Every route sets ``operation_id`` to its design id (``API-09``, ``API-IRR-06``, ...), the canonical
  ``operationId`` in docs/design/dashboard/openapi/dashboard-api.v1.yaml; tests/test_operator_api_contract.py
  checks every served route against it.
- No auth dependencies: ``include_v1()`` adds the bearer, session and role checks (GET and HEAD for viewers and
  operators, every other method for operators only) and sets ``request.state.actor`` to the signed-in person's
  ``verdify_services.actor.Actor``, which a write passes to its service. A route never reads an actor, source or
  ``*_by`` value from the request.
- No SQL: a route only calls ``verdify_services``. A ``ServiceError`` propagates, and the operator API renders
  it as ``application/problem+json`` (``problem.body()`` with ``problem.status``). A write takes
  ``writes.IdempotencyKey``, passes it as ``request_id`` and returns ``WriteResult.rest_headers()`` with its body.
"""
