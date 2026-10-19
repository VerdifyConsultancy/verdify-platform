# #988 evidence custody

The API source617ab6db and API-only release7fc3956d are pushed to main. This
review contains the original failure windows, the exact existing route and
authentication chain, both runtime image/source/module identities, the exact
single-Deployment operation, safe public auth/denial/readiness receipts, and
terminal source/release CI proof. The report and canonical plan accept #988 after actual public JPEG and two later
natural/exact-backend recovery windows pass. The failed first observation attempt
is retained separately; it is not a successful window.

[Acceptance matrix](acceptance.json) preserves all four original clauses.
[Source CI](source-ci-terminal.json) includes all actual declared container
termination and complete selected logs for gate, one build and publisher.
[Release CI](release-ci-terminal.json) and [full gate log archive](ci-logs.tar.gz)
prove the normal validation-only run completed. [Report CI](report-ci-terminal.json) completed Succeeded05:43:09 with one
validation Pod, no builds and all declared containers exit0/zero restarts. Final
closure CI and native closed-state readback are appended only after actual
completion; no future pass is claimed.

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
