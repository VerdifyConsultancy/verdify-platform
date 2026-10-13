# PR #975 delivery disposition — prepared, no release started

The three #975 files are merged at `36b1b63d` and remain byte-identical on current main `916b24a6`. Its automatic workflow `verdify-platform-ci-496hd` passed source validation, selected only ingestor because `COPY docs/ /app/docs/` includes the changed operator runbook, then failed registry admission with `signal-invalid`. Fetch, build-input capture, image tests, Kaniko, publication and the pin actuator never started. No image/digest was produced. Keep that attempt Failed.

The reviewed #643/276 release can absorb this publication gap if its actual affected-image classification includes ingestor and its successful build uses an exact descendant source containing #975. An incremental diff that selects only migrate does not rebuild ingestor merely because #975 is an ancestor. The forthcoming source/COPY packet determines the cohort. Ingestor also copies all `scripts/`; a new tracked maintenance bootstrap script there is an actual input, subject to that exact packet's classification.

Current managed candidates and release pins agree for API, MCP, migrate and orchestrator. Ingestor differs:

| Identity | Digest/source |
| --- | --- |
| Accepted production/release | `sha256:c6cc8d250ef8dabe3bc3bfa4c5bc759c845508a029eae52ea4b3fadde5cd773f` |
| Unpromoted candidate | `sha256:6856b46476ca4edbbf2a13bdc4f989d450de58e0e4d20ea2abedc38fc116a885` |
| Candidate build source | `089a1b1e8a9526963409a5b1c484826fee05ed65`, before #975 |
| Candidate actuator commit | `cc11025310930bb26c617d692b373f0d9f99c0a6` |

The pin actuator preserves unaffected candidates. Do not run a blind default five-image promotion if the new cohort leaves ingestor unaffected: that would promote the older candidate, not resolve #975. If the cohort rebuilds ingestor, require its fresh exact-source digest to replace the older candidate before the normal reviewed promotion. If not, leave #975 outstanding and retain the prior ingestor release pin; any separate supported attempt belongs to the existing CI owner and must be identified explicitly. Do not manufacture a source touch, alter Docker ignore/classifier rules, manually submit an ordinary fleet image build, or reuse a default retry handle.

After the qualified source/credential packet is released, use one combined PR/main build → candidate pin receipt → schema-compatible release promotion → attended full non-pruning sync → runtime qualification. Preserve rollback coordinates, execute migrations through fresh full-sync hooks, and replace the single ingestor only once for the combined cohort. Source merge, publication, pinning, deployment and runtime proof remain distinct. A later successful delivery does not rewrite the failed historical workflow.

The JSON companion `pr975-delivery-disposition.json` binds source hashes, the native classifier/template, historical failure, current candidate/release comparison and conditional delivery mechanics. No build, retry, publication, pin change, production probe, credential operation, SQL or sync was performed during this preparation. Await the genuine #643 qualification/source/credential packet; sole main/production writer custody remains with this lane.
