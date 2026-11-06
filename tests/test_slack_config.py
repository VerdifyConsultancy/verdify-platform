"""#1352: Slack is not a Verdify channel. The Slack config and command modules stay retired.

This file keeps its name because the in-cluster CI test list (.agent-fleet/ci.yaml, generated
in jvallery/agents) still names it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED = ("slack_config.py", "slack.yaml", "slack_ops", "SLACK.md")
DOCKERFILES = ("api/Dockerfile", "ingestor/Dockerfile", "mcp/Dockerfile", "scripts/Dockerfile.lab-publisher")


def test_slack_modules_are_gone():
    assert [name for name in RETIRED if (ROOT / name).exists()] == []


def test_no_image_copies_a_slack_module():
    for name in DOCKERFILES:
        text = (ROOT / name).read_text()
        assert not re.search(r"^\s*COPY\b.*slack", text, re.IGNORECASE | re.MULTILINE), name
