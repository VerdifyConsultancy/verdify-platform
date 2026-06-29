# Sprint state machine

Use `../../verdify.workflow.yaml` as the canonical transition model.

A lane normally moves:

```text
READY -> IMPLEMENTING -> VALIDATING -> READY_FOR_CRITIC
  -> CHANGES_REQUESTED -> IMPLEMENTING
  -> READY_FOR_INTEGRATION
```

`BLOCKED` and `DECISION_REQUIRED` are not completion states. A lease expiry does not cancel issue scope; it requires reconstruction and a new authorized session/worktree decision.
