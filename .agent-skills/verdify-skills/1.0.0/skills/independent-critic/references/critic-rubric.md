# Critic rubric

Review in this order:

1. Contract and issue alignment.
2. Scope and ownership.
3. Functional correctness and acceptance behavior.
4. Security, privacy, and data integrity.
5. Failure modes, idempotency, concurrency, and rollback.
6. Test quality and false-positive risk.
7. Maintainability and architecture consistency.
8. Integration and deployment risk.
9. Evidence completeness and revision freshness.

Severity guidance:

- `critical`: unsafe, exploitable, destructive, or fundamentally wrong outcome;
- `high`: blocks contract acceptance or creates likely system failure;
- `medium`: material defect or weak evidence requiring correction;
- `low`: bounded improvement that may be accepted as risk;
- `note`: observation without requested change.
