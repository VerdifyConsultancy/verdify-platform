"""Stub reporting adapter used by the final report node.

This module currently provides a lightweight placeholder for terminal reporting
without introducing a real external dependency. It connects the report node to
a consistent side-effect shape even though Slack delivery is not the product focus.
"""

from __future__ import annotations


class SlackClient:
    def send_report(self, trigger_id: str, summary: str) -> dict[str, object]:
        return {"sent": False, "trigger_id": trigger_id, "summary": summary}
