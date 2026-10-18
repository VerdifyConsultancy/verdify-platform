# Tier-1 owner exclusions — #801 completed

The two outside-owner hosts are now explicitly excluded from Verdify's tier-1 fallback route. The complete live route matches the reviewed source, and both owner routes retain their identities and specifications.

Source `c386f834` was delivered in release `fc057fb8`. The full nonpruning, selector-free sync succeeded at **01:13:16 UTC October18 (October17 Denver)** across129 resources. All five serving Traefik instances passed the bounded route-error check. The two outside-owner sites and API/Lab/Labs/Grafana return200; MCP root and an unknown host retain their baseline404. All eight checks pass.

[#801](https://github.com/VerdifyConsultancy/verdify-platform/issues/801) is closed as completed. [Curated receipt](receipt.json) records the accepted scope. Detailed operator/native evidence remains private.

The same reviewed delivery rolled the ingestor for #749 and adopted the approved exporter pause. This route result does not accept #749 readiness or assert unchanged ingestor identity. The historical selective-only recipe was superseded by the supported full sync. Route rollback removes only the two added clauses; physical recovery is separate.
