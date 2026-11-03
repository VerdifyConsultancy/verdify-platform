"""Irrigation services (architecture §6, irrigation §8): what the operator API and MCP share for water.

- ``writes``: run-now and stop (API-IRR-06 and API-IRR-07, MCP ``irrigation_run`` and ``irrigation_stop``),
  through the L01 bridge until the irrigation engine firmware is on the controller (VD-64, #1177). #1156
  adds the engine path and the other writes.
- ``reads``: API-IRR-01, -02, -10 and -11 and the MCP read tools (VD-40, #1155), over ``runs`` (relay-edge
  runs and their causes) and ``program`` (what the controller runs).

Every write here follows verdify_services/README.md: one ``activity.recorded_write`` per request, refusals
as ``ServiceError``, and only the ingestor ever calls the device.
"""
