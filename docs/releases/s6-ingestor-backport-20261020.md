# S6 saturation release backport

The live ingestor digest `4b4994bc68c9e0966d2665d4d4da3e4e7e5d1c2dc8b2694f95db1fa629dc6000`
reports OCI source `c386f834de7a1acbd1ad74300218ab7922cf6d4b`; its actual
`ingestor.py` SHA256 independently matches that Git blob. The release-pins header
is historical and was not used as source proof.

This release branch starts at that exact live source and carries only #1019's
stdlib saturation evaluator, three-line scheduler registration and 19 focused
fixtures. All other runtime source, schemas, alerts, dispatcher, component control
and dependencies retain the live base. Main contains unrelated unpromoted alert,
component and wire-model changes; this backport deliberately preserves their gate.

Build this exact branch commit with the existing in-cluster
`repo-build-verdifyconsultancy` template. Record the returned source/digest and
promote only that reviewed ingestor digest alongside separately reviewed API
changes. Do not copy main's ingestor candidate as though it were this backport.
The evaluator remains unavailable/uncommissioned and performs no DB/device call.
