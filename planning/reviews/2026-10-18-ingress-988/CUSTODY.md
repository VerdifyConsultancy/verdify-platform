# #988 evidence custody

The API source617ab6db and API-only release7fc3956d are pushed to main. This
review contains the original failure windows, the exact existing route and
authentication chain, both runtime image/source/module identities, the exact
single-Deployment operation, safe public auth/denial/readiness receipts, and
terminal source/release CI proof. The report and canonical plan explicitly keep
#988 open until actual public JPEG and natural/exact-backend recovery acceptance.

[Acceptance matrix](acceptance.json) preserves all four original clauses.
[Source CI](source-ci-terminal.json) includes all actual declared container
termination and complete selected logs for gate, one build and publisher.
[Release CI](release-ci-terminal.json) and [full gate log archive](ci-logs.tar.gz)
prove the normal validation-only run completed. Final report CI and final native
readback are appended only after they actually finish; no future pass is claimed.

[Public authentication](public-auth-continuation.json),
[post-rollout API guard](public-auth-after-api.json),
[public source readiness](public-readiness-after.json),
[camera denial boundaries](public-camera-denials.json), and
[runtime adoption](runtime-after-verified.json) are separate acceptance surfaces.
The initial empty-notification parser error is retained as a harness limit, and
does not erase successful checks or turn private/LAN smoke into public proof.

[Historical metric provenance](metrics-provenance.json) retains the exact
backend/range/units; extrapolated rates and increases are not integer requests.
Loki line addresses are redacted while timestamps/router/status/duration remain.
[Private custody](PRIVATE-CUSTODY.json) binds original response/log/module hashes
without exporting private JPEGs, credential values, greenhouse response bodies or
unrelated inventory. Existing credential references and published image/source/
Workflow identities are metadata, not credentials. Credential-pattern scans found
zero hits in this curated publication.

[Native prepublication graph](native-before.json) covers every local open issue
and immediate parent/blocking links. Its three source-contract differences are
the planned#75/#981/#988 updates. Home Assistant#878 is accepted closed history;
#981 remains open for its own consumer adoption. No new edge or parent is created.
[Report render equality](report-render-equality.json) proves this documentation/
planning update requires no deployment. Whole-application sync remains prohibited.

CONTENT-MANIFEST.json binds every curated review file except itself. Final remote
verification compares actual GitHub blob identities and local committed bytes;
an immutable final custody link is posted to#988 after that readback succeeds.
