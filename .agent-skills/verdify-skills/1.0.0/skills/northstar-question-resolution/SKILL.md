---
name: northstar-question-resolution
description: Inventories, clusters, researches, and resolves large sets of human-gated or open North Star planning questions across docs, issue exports, interview packets, and `.agent-workflow` artifacts. Use when Codex must process many `[QUESTION:*]`, `NSQ-*`, `NQI-*`, human-gate, decision, architecture, product, schema, storage, security, or delivery questions with delegated authority, Brave Search or other evidence sources, North Star evidence ingestion, and explicit escalation for the few decisions that still require humans.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "2d"
---

# North Star Question Resolution

Turn a large question corpus into evidence-backed answers and a small remaining
human decision pack. This skill supports `northstar-planning`; it does not
replace final North Star lock approval or risk gates.

## Canonical outputs

- `.agent-workflow/northstar/question-resolution/<run-id>/inventory.yaml` -
  deterministic question inventory generated from the target corpus.
- `.agent-workflow/northstar/question-resolution/<run-id>/resolution-register.yaml` -
  question clusters, research plan, evidence refs, delegated answers, confidence,
  affected artifacts, and remaining escalations.
- `.agent-workflow/northstar/research-inbox/*.md` - source-backed research notes
  to ingest with `northstar-research-ingest`.
- `.agent-workflow/northstar/collateral/*` and
  `.agent-workflow/northstar/evidence-registry.yaml` - evidence created by
  `northstar-research-ingest`.
- Proposed updates for `northstar-planning` in `artifact-loop` or
  `review-feedback` mode.

## Procedure

1. Read `../../COMMON_OPERATING_CONTRACT.md`.
2. Read the active user delegation, repository `AGENTS.md`, current
   `.agent-workflow/router/route-decision.yaml`, North Star artifacts,
   `.agent-workflow/northstar/evidence-registry.yaml`, and any current human
   gates.
3. Run `scripts/question_inventory.rb` against the target docs or artifact tree.
   Prefer a run ID such as `2026-06-24-gravity-docs`.
4. Cluster questions by theme, architecture domain, protected-decision class,
   dependency, and likely shared answer. Use `scripts/cluster_questions.rb` for
   the first deterministic cluster map, then refine clusters manually when a
   shared decision needs to split. Use `assets/resolution-register.template.yaml`
   for the register.
5. For each cluster, answer from already registered evidence first. When evidence
   is thin, create a research note under `.agent-workflow/northstar/research-inbox/`.
6. Use Brave Search only through locally configured credentials. Reference
   credential location/auth mode only; never print, copy, commit, or summarize
   raw keys. Prefer primary sources and official docs for technical claims.
7. Ingest each research note:

   ```bash
   ../../bin/verdify northstar ingest-research \
     --repo <repository> \
     --file <research-note> \
     --title "<title>" \
     --summary "<why it matters>" \
     --tag question-resolution \
     --claim "<source-backed claim>"
   ```

8. Record each delegated answer with options, selected answer, confidence,
   evidence refs, affected artifacts, rationale, and whether human escalation
   remains required.
9. Batch true human-only items into a short thematic list. A useful pack should
   unblock many detailed questions; do not forward hundreds of raw questions to
   the human.
10. Hand off to `northstar-planning` in `artifact-loop` or `review-feedback` to
    resolve `NSQ-*`/`NQI-*` items, update North Star artifacts, and preserve
    final lock approval separately.

## Delegated authority rules

- Use delegated authority for low- and medium-risk architecture, product,
  operational, tooling, and backlog defaults when evidence supports one option
  clearly.
- Keep a protected escalation when the answer changes public APIs, schemas or
  frontmatter, storage/retention/deletion policy, security boundaries, identity,
  production networking, destructive operations, external dependencies, material
  cost, or deployment risk and the delegation/evidence is not explicit enough.
- Do not treat an answer as final North Star lock approval.
- Mark claims as `verified`, `observed`, `reported`, `inferred`, or `unknown`.
- If several questions share the same thematic decision, answer once at the
  theme level and map each question to that answer.

## Resources

- Use `scripts/question_inventory.rb` to scan markdown and YAML-ish planning
  artifacts for explicit question markers by default. Add `--include-inferred`
  when ordinary question-mark lines should be included.
- Use `scripts/cluster_questions.rb` to map all inventoried questions into
  thematic decision clusters before research starts.
- Read `references/brave-research.md` before using Brave Search.
- Read `references/resolution-register.md` when creating or reviewing the
  resolution register.
- Use `assets/resolution-register.template.yaml` to start a run register.
