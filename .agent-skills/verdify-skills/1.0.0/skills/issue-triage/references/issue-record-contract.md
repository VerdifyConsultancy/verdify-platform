# Issue Record Contract

Read this before creating or updating GitHub issues.

## Template Selection

1. Prefer repository issue forms in `.github/ISSUE_TEMPLATE/*.yml` or `.github/ISSUE_TEMPLATE/*.yaml`.
2. Use the problem/outcome template for bugs, product gaps, UX failures, operational gaps, and code quality problems.
3. Use the decision template only when a material choice blocks safe work, changes public interfaces, affects data/security/production, or needs an authorized resolver.
4. If no usable template exists, preserve the same fields as Markdown sections in the issue body.

## De-duplication

Search before creating:

- issue title keywords and user wording;
- exact error messages, stack traces, failing command names, URLs, and UI labels;
- affected paths, classes, functions, package names, config keys, and product surfaces;
- open and recently closed issues.

If an existing issue covers the same problem and desired outcome, comment or update that issue with the new evidence. Create a new issue only when the outcome, affected surface, or acceptance intent is materially different.

## Required Fields

Use repository template field names when present. Otherwise include:

- **Problem**: what is wrong, missing, risky, or costly. Separate `reported`, `observed`, `verified`, `inferred`, and `unknown` claims.
- **Desired outcome**: the observable state that should be true when solved.
- **Acceptance intent**: testable bullets, not a hidden implementation plan.
- **Non-goals**: fixes or surfaces that should stay out of this issue.
- **Dependencies and related issues**: duplicates, blockers, related regressions, decisions, or follow-up issues.
- **Initial risk**: low, medium, high, critical, or unknown. Add matching repository labels only when they already exist or policy permits creating them.
- **Affected users, modules, data, or environments**: include paths or components when verified.
- **Evidence and context**: commands, logs, screenshots, code references, recent commits, and links. Redact secrets and sensitive data.

## Investigation Appendix

Add a compact appendix when the template has no dedicated fields:

```markdown
### Triage investigation
- Existing issue search:
- Evidence inspected:
- Reproduction:
- Likely cause:
- Potential fix options:
- Adversarial audit:
- Confidence:
- Remaining unknowns:
```

## Safety

- Never include credentials, private keys, tokens, production secrets, customer personal data, or exploit-ready vulnerability details in an issue body.
- Use the repository security policy or private channel for unpatched vulnerabilities.
- Do not assign owners, milestones, Projects, or sprint labels unless the user or repository policy gives authority.
- Do not close issues during triage unless the user explicitly asks and the duplicate/invalid reason is evidenced.
