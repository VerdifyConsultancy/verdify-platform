# Lab occurrence reporting boundary (inactive)

This Component records the Phase 4c trust boundary but deliberately creates no
producer or reporting tier. It is not referenced by any overlay. If it were
referenced accidentally, its only pod selector is deny-all and there is still no
workload to select.

Activation is a separate Jason-gated change. That change must create an
operator-owned, one-way, read-only public reporting projection and credential;
must not reuse the anonymous `graphs.verdify.ai` surface or the Track A database
role; and must replace deny-all with egress limited to the approved reporting
feed, the two exact public camera GET paths on `api.verdify.ai:443`, and the
occurrence store. The exporter must never reach the device VLAN, Frigate,
go2rtc, the primary database, MCP, or any controller surface.

Camera responses are an input only. The producer must reject redirects and
authentication, decode the JPEG, re-encode clean RGB/RGBA PNG bytes without
metadata, and name the candidate by its SHA-256 before the offline occurrence
compiler will accept it. A privacy-safe request-provenance digest binds the
occurrence, GET method, exact URL, and no-redirect/no-auth/no-cookie policy through
the private handoff; the public release continues to expose opaque occurrence IDs
only. Batches are also exact-policy-digest bound and must reach the trusted compiler
clock within five minutes, with at most 60 seconds of bounded future skew.
