# Switchback protocol lock ceremony

This directory holds the machine-readable preregistration for the planner
switchback (audit Sections 8.2–8.6,
[docs/research/planner-efficacy-current-firmware-2026-08-14.md](../../../docs/research/planner-efficacy-current-firmware-2026-08-14.md),
issue #588).

`planner-switchback-v1.template.yaml` is a **scaffold, not a locked protocol
instance**. Every `TO-LOCK:` placeholder must be resolved and the file renamed
to `planner-switchback-v1.yaml` at protocol lock. A locked instance is
immutable; any change afterwards ends the protocol epoch.

## Ceremony order (strict)

1. **Protocol commit.** Fill every `TO-LOCK:` field except the beacon output
   hash and the schedule artifact/hash (those depend on the future beacon):
   study/greenhouse IDs, dates, namespace UUID, baseline and template content
   hashes (after replay, HIL, and A/A approval), qualification spec/result
   hashes, all revision pins, margins sign-off, analysis environment digest,
   and tested rollback vector. Name the **future** public-randomness beacon
   round. Commit `planner-switchback-v1.yaml`.
2. **Mapping-secret draw and commitment.** With witnesses, generate the
   32-byte mapping secret using an operating-system CSPRNG. The tooling never
   generates it. Record the commitment **before** the beacon round publishes:

   ```sh
   cd research/planner-efficacy
   uv run python -m switchback commit-mapping \
       --study-id "$STUDY_ID" --secret-file /secure/path/mapping-secret.bin
   ```

   Only the assignment service may read the secret before analysis lock;
   analysts and dashboards see only blinded `X`/`Y` labels.
3. **Beacon publishes.** After the named round's output is public, record its
   raw-byte hash in the protocol instance.
4. **Schedule generation.** Derive the blinded 30-day schedule from the beacon
   output and commit the JSON plus its RFC 8785 hash:

   ```sh
   uv run python -m switchback gen-schedule \
       --study-id "$STUDY_ID" --start-local-date YYYY-MM-DD \
       --namespace-uuid "$NAMESPACE_UUID" \
       --beacon-file beacon-round.bin \
       --out planner-switchback-v1.blinded-schedule.json
   ```

Anyone can then reproduce the whole chain with
`python -m switchback verify --schedule ... --beacon-file ...`; after the
frozen analysis output is hashed and signed, the reveal step
(`python -m switchback reveal`) resolves `X`/`Y` to physical `A`/`B` and
regenerates the published commitment byte-for-byte.

Do not rerandomize, shift, or replace any assignment for any reason after
lock. A failed gate produces a new protocol version, a new secret, and a new
beacon round — never an edit to a locked instance.

## Protocol deviation (recorded 2026-08-15, decision: Jason)

The §8.3 *witnessed* mapping-secret ceremony is replaced by an **automated,
publicly-committed draw**: the assignment service generates the 32-byte secret
with the OS CSPRNG and its domain-separated commitment
(`verdify-switchback-map-commit-v1`) is published in a git commit **before**
the named public beacon round publishes. Git commit ordering + the beacon
round replace the human witness. Blinding, the byte-exact HMAC derivations,
and the restricted-service secret custody are unchanged. What is lost is
third-party attestation that no redraw occurred before commitment; the public
commit ordering substantially covers this. Qualification and A/A gates are
NOT waived — the migration-207 state machine binds their result hashes before
a randomized experiment can arm.
