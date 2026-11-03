"""Irrigation services (architecture §6, irrigation §8): what the operator API and MCP share for water.

- ``writes``: schedules, lines, run-now, stop, bursts and clear-fault (API-IRR-03 to -08, -12 and -13, VD-41, #1156,
  and the MCP write tools). Before the irrigation engine firmware, run on L01 and every Stop go through the L01
  bridge (VD-64, #1177).
- ``reads``: API-IRR-01, -02, -10 and -11 and the MCP read tools (VD-40, #1155), over ``runs`` (relay-edge
  runs and their causes) and ``program`` (what the controller runs).

Every write here follows verdify_services/README.md: one ``activity.recorded_write`` per request, refusals
as ``ServiceError``, and only the ingestor ever calls the device.
"""
