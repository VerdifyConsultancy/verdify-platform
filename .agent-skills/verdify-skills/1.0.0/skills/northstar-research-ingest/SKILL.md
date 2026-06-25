---
name: northstar-research-ingest
description: Ingests research notes, reports, source documents, benchmark findings, external references, and adversarial review outputs into the North Star planning collateral folder and evidence registry. Use when Codex needs a command-backed way to add research to `.agent-workflow/northstar/collateral/`, update `.agent-workflow/northstar/evidence-registry.yaml`, and make evidence referenceable and queryable before `northstar-planning` synthesis.
compatibility: Requires a target repository initialized with Verdify or permission to create `.agent-workflow/northstar/`. The Verdify CLI command copies local research files and writes YAML artifacts; do not ingest raw secrets or restricted data.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "2a"
---

# North Star Research Ingest

Normalize research into durable planning evidence. Do not synthesize the North
Star plan here; hand off to `northstar-planning` after the evidence registry is
updated.

## Command

```bash
../../bin/verdify northstar ingest-research \
  --repo <repository> \
  --file <research-file> \
  --title "<title>" \
  --summary "<why it matters>" \
  --tag <tag> \
  --claim "<source-backed claim>"
```

Query the registry:

```bash
../../bin/verdify northstar evidence list --repo <repository> --query <text> --json
```

## Canonical artifacts

- `.agent-workflow/northstar/evidence-registry.yaml` - queryable registry
- `.agent-workflow/northstar/collateral/<evidence-id>.yaml` - normalized
  evidence item
- `.agent-workflow/northstar/collateral/sources/<evidence-id>-<name>` - copied
  source file

Validate registry YAML against
`../../schemas/northstar-evidence-registry.schema.yaml` and item YAML against
`../../schemas/northstar-evidence-item.schema.yaml`.

## Procedure

1. Read `../../COMMON_OPERATING_CONTRACT.md`.
2. Confirm the source file contains research or planning evidence, not raw
   secrets, credentials, private keys, customer data, or production data.
3. Run `northstar ingest-research` with title, summary, tags, and source-backed
   claims.
4. Confirm the command prints the evidence ID and reference URI.
5. Query the registry by tag or text to prove the item is discoverable.
6. Hand off to `northstar-planning` when enough evidence is registered.

## Stop conditions

Stop before ingesting secrets, regulated data, unclear third-party content, or a
source whose license/provenance is unknown. Record a gate or question instead.

## Load references only when needed

- Read `references/evidence-registry.md` for ID, tag, claim, and query rules.
