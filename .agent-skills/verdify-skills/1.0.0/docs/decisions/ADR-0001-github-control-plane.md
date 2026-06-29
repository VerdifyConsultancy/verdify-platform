# ADR-0001: GitHub as typed control plane

- Status: accepted
- Date: 2026-06-22

## Decision

Use GitHub Issues for backlog problems, PRs for proposed changes, required checks and reviews for quality, releases/tags for release identity, and deployments/environments for runtime delivery. Versioned Verdify artifacts define approved project intent and execution contracts. Local snapshots are caches.

## Consequences

The system avoids a competing task database but must explicitly reconcile GitHub with versioned contracts. “Source of truth” is defined by information type rather than as an undifferentiated slogan.
