# Planner semantic dependency lifecycle (#214)

`lessons_search` and `knowledge_search` require the configured OpenAI
`text-embedding-3-large` provider (3072 dimensions), its SDK in the MCP image,
and `OPENAI_API_KEY`. Search failure remains an error even when Iris later
writes a valid plan. The tools emit one canonical
`planner_tool_dependency_failed` warning per tool, classified as missing
credential, provider failure, or semantic query failure. No query, credential,
or provider response text is stored in the alert. Provider calls are bounded
at 30 seconds without SDK retries.

Only a successfully completed embedding **and** database retrieval by the
same semantic tool resolves its warning; a valid empty result also qualifies.
`set_plan`, gather recovery, and the manual `alerts(resolve)` tool cannot claim
this recovery. Atomic per-tool transactions deduplicate concurrent failures;
a successful request started before a later failure cannot resolve that later
failure. If canonical storage is unavailable, the tool explicitly reports
that failure rather than claiming the alert was recorded or resolved.

## Genuine deployment acceptance

The current MCP deployment has no embedding-key reference, and its old image
has no OpenAI SDK. Preserve that existing degraded configuration during phase
one: deploy the lifecycle/SDK source using the normal reviewed image promotion
and attended full sync. Make genuine read-only semantic searches through the
existing authenticated MCP transport. Retain the returned classified error,
original tool-call identity, matching canonical warning identity and native
timestamps. Do not manufacture an outage or remove a working credential.

Phase two adds only the existing `verdify-hermes:OPENAI_API_KEY` reference and
its Secret consumer documentation. After the exact MCP configuration rollout,
repeat genuine searches through the same transport. Capture actual successful
semantic results and resolution of those same original warning rows. Code
image digests can remain unchanged in this configuration-only phase. Existing
credentials are not rotated. ROOT coordinates delivery after current C1
restoration; source/CI and isolated PostgreSQL fixture results do not constitute
this live failure/recovery acceptance.

Historical September gather failures and October semantic errors remain
original records. They are not backfilled as newly canonicalized events.
