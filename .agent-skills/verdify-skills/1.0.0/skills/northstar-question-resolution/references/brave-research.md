# Brave Research Rules

Use this reference when a question cluster needs external research.

## Credential handling

- Use an existing local credential source such as `~/Agents/root/secrets`,
  `.agent-workflow/northstar/credential-references.yaml`, or another approved
  operator credential reference.
- Export only the required environment variable for the command being run, for
  example `BRAVE_SEARCH_API_KEY`.
- Never print, paste, commit, log, or summarize raw secret values.
- Do not copy credentials into the repository or research notes.

## Search discipline

1. Start from the clustered decision, not from the raw question text.
2. Prefer primary sources: official project docs, standards, cloud/vendor docs,
   GitHub repositories, API references, whitepapers, release notes, or published
   benchmark methodology.
3. Use secondary sources only to discover primary sources or to compare market
   framing. Label them as secondary.
4. Capture the search query, source URL, source type, retrieved date, and a
   short paraphrased finding. Avoid long quotations.
5. Record limitations, missing evidence, and disagreement between sources.

## Research note shape

Each note should include:

- question cluster ID and covered question IDs;
- search queries used;
- source table with URL, source type, date observed, and relevance;
- options considered;
- source-backed findings;
- recommended default and confidence;
- protected-decision or human-escalation status;
- claims ready for `northstar-research-ingest`.

## Safety checks

Stop and record an escalation instead of researching live systems when a question
requires raw secrets, customer data, production mutation, destructive action,
private third-party content, or a license/provenance that cannot be determined.
