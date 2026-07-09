# Repository hygiene — blocked on credential rotation

All non-secret prerequisites are ready: repository/main identity, isolated worktree, approved lifecycle artifacts, live GitHub issue authority, no open PRs, discoverable tests/CI, single-prod delivery/rollback, observability, and exclusive module ownership. Historical S8 and PR #409 are explicitly superseded. Old worktrees are attributable and preserved rather than destructively cleaned.

The redacted secret/source scan found that five tracked renderer/snapshot/writer scripts contained a fallback matching the current production application database password. The fallback has been removed and the scripts now require `VERDIFY_DSN` or `POSTGRES_PASSWORD`; the value was never displayed. Because the same value exists in Git history and is still live, repository hygiene cannot be declared complete until Jason explicitly authorizes rotation or accepts a release block.

Gate: `gates/g-prod-db-credential-rotation.yaml`.

Eight other current-tree gitleaks detections were reviewed as SQL/test-key false positives. The many historical detections will be baselined only after the real exposed value is invalidated.
