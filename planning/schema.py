"""Validate the complete issue campaign, coverage, hierarchy and blocking DAG."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage(StrictModel):
    id: str
    title: str
    exit: str
    owner: str
    estimate: str


class WorkItem(StrictModel):
    key: str
    issue_number: int | None
    title: str
    stage: str
    kind: Literal["task", "epic"]
    priority: Literal["P0", "P1", "P2"]
    effort: Literal["S", "M", "L"]
    owner_role: str
    parent: str | None
    depends_on: list[str]
    what: str
    why: str
    how: list[str]
    acceptance: list[str]
    paths: list[str]
    baseline: str
    delivery: str
    rollback: str
    decision: str

    @field_validator("title", "what", "why", "baseline", "delivery", "rollback", "decision", "owner_role")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required issue detail is empty")
        return value

    @field_validator("how", "acceptance", "paths")
    @classmethod
    def nonempty_list(cls, value: list[str]) -> list[str]:
        if not value or any(not v.strip() for v in value):
            raise ValueError("required issue detail list is empty")
        return value


def check_dag(edges: dict[str, list[str]], label: str) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError(f"{label} cycle at {key}")
        if key in visited:
            return
        visiting.add(key)
        for dependency in edges[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in edges:
        visit(key)


class Backlog(StrictModel):
    version: Literal[2]
    campaign: str
    source_revision: str
    snapshot_date: str
    original_open_issues: list[int]
    stages: list[Stage]
    issues: list[WorkItem]

    @model_validator(mode="after")
    def consistent(self) -> Backlog:
        items = {item.key: item for item in self.issues}
        numbers = [i.issue_number for i in self.issues if i.issue_number is not None]
        stages = {s.id for s in self.stages}
        if len(items) != len(self.issues) or len(set(numbers)) != len(numbers):
            raise ValueError("duplicate issue key/number")
        if len(stages) != len(self.stages):
            raise ValueError("duplicate stage")
        if not set(self.original_open_issues) <= set(numbers):
            raise ValueError("original open issue coverage is incomplete")
        if len(set(self.original_open_issues)) != len(self.original_open_issues):
            raise ValueError("duplicate original issue")
        if any(n <= 0 for n in numbers):
            raise ValueError("invalid issue number")
        for item in self.issues:
            if item.stage not in stages:
                raise ValueError(f"unknown stage: {item.key}")
            refs = item.depends_on + ([item.parent] if item.parent else [])
            if any(ref not in items for ref in refs):
                raise ValueError(f"unknown issue reference: {item.key}")
            if len(set(item.depends_on)) != len(item.depends_on):
                raise ValueError(f"duplicate dependency: {item.key}")
            if item.parent in item.depends_on:
                raise ValueError(f"child blocked by parent: {item.key}")
            if any(items[ref].kind == "epic" for ref in item.depends_on):
                raise ValueError(f"blocking dependency must name a bounded task: {item.key}")
            prose = " ".join([item.what, item.why, *item.how, *item.acceptance])
            for ref in re.findall(r"#([a-z_]+)\b", prose):
                if ref not in items:
                    raise ValueError(f"unknown prose reference {ref}: {item.key}")
        check_dag({i.key: i.depends_on for i in self.issues}, "dependency")
        check_dag({i.key: [i.parent] if i.parent else [] for i in self.issues}, "hierarchy")
        return self


def load(path: str | Path) -> Backlog:
    return Backlog.model_validate(yaml.safe_load(Path(path).read_text()))


def validate_delivery(plan: Backlog, root: Path) -> None:
    if any(i.issue_number is None for i in plan.issues):
        raise ValueError("unpublished issue numbers remain")
    for item in plan.issues:
        for path in item.paths:
            if not (root / path).exists():
                raise ValueError(f"source path does not exist: {item.key}: {path}")


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "planning/backlog.yaml")
    plan = load(path)
    if "--published" in argv:
        validate_delivery(plan, path.resolve().parent.parent)
    print(
        f"OK: {len(plan.issues)} issues, {len(plan.stages)} stages, "
        f"{sum(len(i.depends_on) for i in plan.issues)} dependency edges; coverage and DAG valid"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
