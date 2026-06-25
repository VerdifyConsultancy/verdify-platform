# Contributing

Changes should begin with a GitHub issue that states the problem and desired outcome. Pull requests must link that issue with a supported closing keyword; discovered maintenance work belongs in the backlog rather than an undocumented exception.

Before opening a pull request:

```bash
make test
```

Skill changes must keep `SKILL.md` below 500 lines, use progressive disclosure, preserve one canonical schema location, and add or update evaluation scenarios. Workflow changes must update `verdify.workflow.yaml`, relevant documentation, and examples.

Do not add a dependency without explaining why Ruby, Git, Bash, and GitHub CLI are insufficient.
