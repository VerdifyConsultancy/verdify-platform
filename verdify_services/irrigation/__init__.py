"""Irrigation services (architecture §6, irrigation §8): what the operator API and MCP share for water.

- ``writes``: run-now and stop (API-IRR-06 and API-IRR-07, MCP ``irrigation_run`` and ``irrigation_stop``),
  through the L01 bridge until the irrigation engine firmware is on the controller (VD-64, #1177). #1156
  adds the engine path and the other writes; the reads (#1155) join this package beside it.

Every write here follows verdify_services/README.md: one ``activity.recorded_write`` per request, refusals
as ``ServiceError``, and only the ingestor ever calls the device.
"""
