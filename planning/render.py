"""Deterministically render roadmap, issue bodies and graph; never mutate GitHub."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from planning.schema import Allocation, Backlog, WorkItem, load

REPO = "https://github.com/VerdifyConsultancy/verdify-platform"
ROOT = Path(__file__).resolve().parents[1]


def resolve(text: str, plan: Backlog) -> str:
    items = {i.key: i for i in plan.issues}
    return re.sub(
        r"#([a-z_]+)\b", lambda m: f"#{items[m[1]].issue_number}" if items[m[1]].issue_number else f"#{m[1]}", text
    )


def ref(item: WorkItem) -> str:
    return f"[#{item.issue_number}]({REPO}/issues/{item.issue_number})" if item.issue_number else item.key


def issue_title(item: WorkItem) -> str:
    return f"[{item.stage}][{item.priority}] {item.title}"


def issue_body(item: WorkItem, plan: Backlog) -> str:
    items = {i.key: i for i in plan.issues}
    children = [i for i in plan.issues if i.parent == item.key]
    stage = next(s for s in plan.stages if s.id == item.stage)
    parts = [
        f"# {item.title}",
        f"Campaign: #775 · Stage: **{item.stage} — {stage.title}** · Priority: {item.priority} · Effort: {item.effort} · Accountable role: {item.owner_role}.\n\nRoles are execution responsibilities, not a reassignment of existing GitHub assignees. This September 5 contract supersedes conflicting historical issue-body sequencing; prior comments and receipts remain evidence, not current-state assertions.",
        f"## What\n\n{item.what}",
        f"## Why\n\n{item.why}",
        f"## Baseline and remaining work\n\n{item.baseline}",
        "## How / implementation strategy\n\n" + "\n".join(f"{n}. {line}" for n, line in enumerate(item.how, 1)),
        "## Source touchpoints\n\n" + "\n".join(f"- `{path}`" for path in item.paths),
        "## Dependencies and scope\n\n"
        + (f"Parent: #{items[item.parent].issue_number or item.parent}. " if item.parent else "Campaign root. ")
        + "Parentage is a roll-up, not a blocking dependency.\n\n"
        + (
            "Blocked by: "
            + ", ".join(f"#{items[key].issue_number or key}" for key in item.depends_on)
            + ". These are full bounded predecessor deliverables; preparation/read-only work may proceed earlier."
            if item.depends_on
            else "No issue-level predecessor. Pull according to stage priority and the concrete safety/physical conditions below; no blanket human gate is implied."
        ),
        "## Acceptance and validation\n\n" + "\n".join(f"- [ ] {line}" for line in item.acceptance),
        f"## Delivery and real end-state verification\n\n{item.delivery}",
        f"## Rollback and evidence preservation\n\n{item.rollback}",
        f"## Decisions / physical boundary\n\n{item.decision}",
    ]
    if children:
        parts.append(
            "## Child deliverables\n\n"
            + "\n".join(f"- [ ] #{child.issue_number or child.key} — {child.title}" for child in children)
        )
    parts.append(
        f"## Campaign references\n\n[Implementation strategy]({REPO}/blob/main/planning/CAMPAIGN.md) · [Full dependency graph]({REPO}/blob/main/planning/DEPENDENCIES.md) · [Validated source]({REPO}/blob/main/planning/backlog.yaml) · [September 5 evidence basis]({REPO}/blob/main/planning/EVIDENCE.md).\n\nOriginal issue bodies and metadata are retained in [the pre-rewrite snapshot]({REPO}/blob/main/planning/archive/2026-09-05/issues.json). Closed issues and existing comments are not rewritten. No production deployment, device change, random draw or experiment launch is performed by this planning sprint."
    )
    return resolve("\n\n".join(parts) + "\n", plan)


def hours(allocation: Allocation) -> str:
    if allocation.hours_min is None:
        return "Unestimated"
    if allocation.hours_min == allocation.hours_max:
        return str(allocation.hours_min)
    return f"{allocation.hours_min}–{allocation.hours_max}"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def sprint_label(sprint_id: str, title: str) -> str:
    return title if title.startswith(f"{sprint_id} — ") else f"{sprint_id} — {title}"


def execution_summary(plan: Backlog) -> str:
    execution = plan.execution_plan
    if execution is None:
        return ""
    parts = [
        "## Current execution plan",
        f"Reviewed **{execution.reviewed_at}** against source `{execution.source_revision}`. "
        f"[{execution.review_path}]({execution.review_path}) records current source, GitHub, runtime and evidence limits. "
        f"All **{len(execution.open_issues)} open issues** have one allocation and accountable role. "
        f"Planning capacity: **{execution.capacity_hours_per_week} engineering hours/week**; ranges are estimates, not calendar promises.",
        "| Pull set | Kind | Allocated hours | Capacity hours | Issues |\n|---|---|---|---|---|",
    ]
    for sprint in execution.sprints:
        allocations = [allocation for allocation in execution.allocations if allocation.sprint == sprint.id]
        estimated = [allocation for allocation in allocations if allocation.hours_min is not None]
        if estimated:
            minimum = sum(allocation.hours_min for allocation in estimated)
            maximum = sum(allocation.hours_max for allocation in estimated)
            total = str(minimum) if minimum == maximum else f"{minimum}–{maximum}"
            if len(estimated) != len(allocations):
                total += " + unestimated"
        else:
            total = "Unestimated" if allocations else "0"
        references = (
            ", ".join(
                f"[#{allocation.issue_number}]({REPO}/issues/{allocation.issue_number})" for allocation in allocations
            )
            or "None"
        )
        capacity = str(sprint.capacity_hours) if sprint.capacity_hours is not None else "Not timeboxed"
        parts.append(
            f"| {cell(sprint_label(sprint.id, sprint.title))} | {sprint.kind} | {total} | {capacity} | {references} |"
        )
    parts.append(
        "Sprint allocations may cover bounded preparation. Full issue acceptance still depends on its predecessors, "
        "current evidence and declared physical or seasonal conditions. C0–C8 remain stage contracts; "
        "closed historical issues remain visible in the stage views below."
    )
    return "\n\n".join(parts[:3]) + "\n" + "\n".join(parts[3:-1]) + "\n\n" + parts[-1] + "\n\n"


def execution_sprints(plan: Backlog) -> str:
    execution = plan.execution_plan
    if execution is None:
        return ""
    parts = []
    for sprint in execution.sprints:
        capacity = f"Capacity: {sprint.capacity_hours} engineering hours. " if sprint.capacity_hours is not None else ""
        parts.extend(
            [
                f"## {sprint_label(sprint.id, sprint.title)}",
                f"Kind: {sprint.kind}. {capacity}Goal: {sprint.goal}",
                f"Exit: {sprint.exit}",
                "| Issue | Accountable role | Engineering hours | Next action |\n|---|---|---|---|",
            ]
        )
        allocations = [allocation for allocation in execution.allocations if allocation.sprint == sprint.id]
        for allocation in allocations:
            parts[-1] += (
                f"\n| [#{allocation.issue_number}]({REPO}/issues/{allocation.issue_number}) "
                f"| {cell(allocation.owner_role)} | {hours(allocation)} | {cell(allocation.next_action)} |"
            )
        if not allocations:
            parts[-1] += "\n| — | — | — | No open issue allocation. |"
    return "\n\n".join(parts) + "\n\n"


def outputs(plan: Backlog) -> dict[str, str]:
    items = {i.key: i for i in plan.issues}
    common = "Generated from [planning/backlog.yaml](planning/backlog.yaml). Edit the source and run `python -m planning.render`; do not maintain a competing roadmap.\n\nThe [campaign strategy](planning/CAMPAIGN.md) defines scope, release bundles, evidence gates and deferred work. Historical June/August plans are [archived](planning/archive/2026-09-05/README.md), not execution instructions.\n"
    table = ["| Stage | Exit | Owner role | Issues |", "|---|---|---|---|"]
    for stage in plan.stages:
        group = [i for i in plan.issues if i.stage == stage.id]
        table.append(
            f"| {stage.id} — {stage.title} | {stage.exit} | {stage.owner} | {', '.join(ref(i) for i in group)} |"
        )
    board = (
        "# Campaign roadmap — September 5, 2026\n\n"
        + common
        + "\n"
        + "\n".join(table)
        + "\n\nThe previous ProjectV2 #5 link did not resolve and the accessible organization project inventory was empty on September 5. The delivered board is this index plus GitHub milestones, native sub-issues and native blocked-by dependencies; no inaccessible board is claimed updated.\n"
    )
    milestones = (
        "# Campaign milestones\n\n"
        + common
        + "\nMilestones C0–C8 replace active issue placement in the old M8/M8.1/G/S milestones. Old milestones and closed issue membership remain historical; no old milestone is marked completed merely by moving its open work.\n\n"
        + "\n".join(table)
        + "\n"
    )
    sprints = (
        "# Implementation sequence and estimates\n\n"
        + common
        + "\nEngineering-day estimates are planning ranges, not calendar promises or mandatory soak gates. S = up to one engineering day; M = roughly 1–3; L = 3–5 or a roll-up to split into coherent PRs. Stage ranges are bundle estimates, not sums of duplicated epic effort.\n\n"
    )
    for s in plan.stages:
        sprints += f"## {s.id} — {s.title}\n\n{s.estimate}\n\nExit: {s.exit}\n\n"
    sprints += "C0 and independent C1 qualification can overlap. C4/C5 changes may run alongside the study only when they preserve its frozen identities and outcomes; otherwise defer or follow the locked safety-abort/deviation contract. No opportunistic tuning, meter reinterpretation or sensor-panel substitution during assigned days.\n"
    lanes = (
        "# Ownership and issue pull order\n\n"
        + common
        + "\nOne accountable role per issue; preserve existing assignees and choose an execution owner when pulling work. Serialize migrations and writer/release mutations even when analysis and device-denied tests run concurrently.\n\n"
    )
    for s in plan.stages:
        lanes += f"## {s.id} — {s.owner}\n\n| Issue | Outcome | Blocking predecessors | Effort |\n|---|---|---|---|\n"
        for i in (i for i in plan.issues if i.stage == s.id):
            deps = ", ".join(ref(items[d]) for d in i.depends_on) or "None; see concrete issue conditions"
            lanes += f"| {ref(i)} | {i.title} | {deps} | {i.effort} |\n"
        lanes += "\n"
    epics = (
        "# Campaign hierarchy\n\n"
        + common
        + "\nHierarchy means ownership/roll-up only. Native blocked-by edges separately encode execution dependencies. A bounded task may have a child, but no child waits for its umbrella to close.\n\n"
    )

    def tree(key: str, level: int = 0) -> str:
        item = items[key]
        line = "  " * level + f"- {ref(item)} {item.title} ({item.stage})\n"
        return line + "".join(tree(i.key, level + 1) for i in plan.issues if i.parent == key)

    epics += tree("775")
    graph = "# Complete blocking dependency graph\n\nGenerated from [backlog.yaml](backlog.yaml). An arrow means **predecessor must finish before dependent acceptance/activation**, not parentage. Isolated nodes are included so coverage is auditable. Read [CAMPAIGN.md](CAMPAIGN.md) for the smaller critical path.\n\n```mermaid\nflowchart TD\n"
    for s in plan.stages:
        graph += f'  subgraph {s.id}["{s.id}: {s.title}"]\n'
        for i in (i for i in plan.issues if i.stage == s.id):
            graph += f'    i{i.issue_number or i.key}["#{i.issue_number or i.key} {i.title[:58].replace(chr(34), chr(39))}"]\n'
        graph += "  end\n"
    for i in plan.issues:
        for d in i.depends_on:
            graph += f"  i{items[d].issue_number or d} --> i{i.issue_number or i.key}\n"
    graph += "```\n\n## Exact edge list\n\n| Predecessor | Dependent |\n|---|---|\n"
    for i in plan.issues:
        for d in i.depends_on:
            graph += f"| {ref(items[d])} | {ref(i)} |\n"
    graph += "\nClosed #676 remains historical qualification evidence, not an open blocker. Cross-repository fleet/monitoring delivery is a concrete implementation interface in the relevant issues, not a fabricated in-repository node. Physical windows and minimum live-state invariants remain explicit issue conditions.\n"
    manifest = dict(
        campaign=plan.campaign,
        issues=[
            dict(
                key=i.key,
                number=i.issue_number,
                title=issue_title(i),
                stage=i.stage,
                parent=items[i.parent].issue_number if i.parent else None,
                blocked_by=[items[d].issue_number for d in i.depends_on],
                body_sha256=hashlib.sha256(issue_body(i, plan).encode()).hexdigest(),
            )
            for i in plan.issues
        ],
    )
    rendered = {
        "PROJECT_BOARD.md": board,
        "MILESTONES.md": milestones,
        "SPRINTS.md": sprints,
        "LANES.md": lanes,
        "EPICS.md": epics,
        "planning/DEPENDENCIES.md": graph,
        "planning/issues_manifest.json": json.dumps(manifest, indent=2) + "\n",
    }
    if plan.execution_plan is not None:
        review_path = Path(plan.execution_plan.review_path)
        review_directory = review_path.parent if review_path.suffix == ".md" else review_path
        review_graph = (review_directory / "DEPENDENCIES.md").as_posix()
        graph_title, graph_body = rendered["planning/DEPENDENCIES.md"].split("\n\n", 1)
        rendered["planning/DEPENDENCIES.md"] = (
            graph_title
            + f"\n\nThe [current reviewed native graph]({review_graph.removeprefix('planning/')}) includes independent local "
            "issues and transitive cross-repository predecessors. The canonical "
            f"{len(plan.issues)}-issue/{sum(len(item.depends_on) for item in plan.issues)}-edge campaign graph "
            "is retained below.\n\n" + graph_body
        )
        titles = {
            "PROJECT_BOARD.md": "# Verdify project roadmap",
            "MILESTONES.md": "# Campaign stages and sprint milestones",
            "SPRINTS.md": "# Upcoming sprints and ongoing work",
            "LANES.md": "# Ownership and issue pull order",
        }
        summary = execution_summary(plan)
        for path, title in titles.items():
            body = rendered[path].split("\n\n", 1)[1]
            if path == "MILESTONES.md":
                body = body.replace(
                    "Milestones C0–C8 replace active issue placement in the old M8/M8.1/G/S milestones. "
                    "Old milestones and closed issue membership remain historical; no old milestone is marked "
                    "completed merely by moving its open work.",
                    "C0–C8 define campaign stage acceptance. Sprint milestones hold the current execution pull sets; "
                    "old milestone and closed issue membership remain historical. Moving an issue does not complete "
                    "its previous milestone or acceptance contract.",
                )
            detail = execution_sprints(plan) if path == "SPRINTS.md" else ""
            rendered[path] = title + "\n\n" + summary + detail + "## Campaign stage contracts\n\n" + body
    return {path: text.rstrip() + "\n" for path, text in rendered.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--body-dir", type=Path)
    args = parser.parse_args()
    plan = load(ROOT / "planning/backlog.yaml")
    for path, text in outputs(plan).items():
        target = ROOT / path
        if args.check:
            if not target.exists() or target.read_text() != text:
                raise SystemExit(f"stale generated file: {path}")
        else:
            target.write_text(text)
    if args.body_dir:
        args.body_dir.mkdir(parents=True, exist_ok=True)
        for item in plan.issues:
            (args.body_dir / f"{item.issue_number or item.key}.md").write_text(issue_body(item, plan))
    print(f"{'Checked' if args.check else 'Rendered'} {len(plan.issues)} issues and seven planning views")


if __name__ == "__main__":
    main()
