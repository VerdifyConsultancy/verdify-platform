"""Validate planning/backlog.yaml against the pydantic schema on every change."""
from pathlib import Path
import planning.schema as schema

ROOT = Path(__file__).resolve().parents[2]


def test_backlog_validates():
    bl = schema.load(ROOT / "planning" / "backlog.yaml")
    assert bl.lanes and bl.waves


def test_every_lane_has_stories_and_items():
    bl = schema.load(ROOT / "planning" / "backlog.yaml")
    for lane in bl.lanes:
        assert lane.user_stories, f"{lane.lane_id} has no user stories"
        for story in lane.user_stories:
            assert story.work_items, f"{story.story_id} has no work items"


def test_new_properties_imply_schema_touch():
    bl = schema.load(ROOT / "planning" / "backlog.yaml")
    for lane in bl.lanes:
        for story in lane.user_stories:
            for wi in story.work_items:
                if wi.new_properties:
                    assert wi.schema_touch, f"{wi.title} introduces props without schema_touch"
