"""Public saved-query Grafana compatibility; never accepts SQL or a target URL.

Grafana 12.4's public page ignores viewPanel. Filter its public metadata only;
queries still go to Grafana's native saved-panel endpoint. No Grafana login or
datasource credential is used. The renderer credential stays inside this process.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("grafana_catalog.json").read_text())
BY_TOKEN = {row["token"]: uid for uid, row in CATALOG.items()}
GRAFANA = "http://verdify-grafana:3000"
RENDERER = "http://verdify-grafana:8081/render"


def public_panel(token: str, referer: str) -> int | None:
    uid = BY_TOKEN.get(token)
    if uid is None:
        raise ValueError("unknown public dashboard")
    ref = urllib.parse.urlsplit(referer)
    if ref.hostname not in {"graphs.verdify.ai", "verdify-grafana"} or ref.path != f"/public-dashboards/{token}":
        return None
    values = urllib.parse.parse_qs(ref.query)
    if "panelId" not in values:
        return None
    panel = int(values["panelId"][0])
    if panel not in CATALOG[uid]["panels"]:
        raise ValueError("unknown saved panel")
    return panel


def panel_metadata(document: dict, panel_id: int | None, height: int = 400) -> dict:
    if panel_id is None:
        return document
    result = copy.deepcopy(document)
    dashboard = result["dashboard"]

    def find(panels):
        for panel in panels:
            if panel.get("id") == panel_id and panel.get("type") != "row":
                return panel
            nested = find(panel.get("panels", []))
            if nested:
                return nested
        return None

    panel = find(dashboard["panels"])
    if panel is None:
        raise ValueError("saved panel unavailable")
    panel["gridPos"] = {"x": 0, "y": 0, "w": 24, "h": max(1, min(40, (height - 110) // 36))}
    dashboard["panels"] = [panel]
    dashboard["refresh"] = ""
    return result


def fetch_metadata(token: str) -> dict:
    if token not in BY_TOKEN:
        raise ValueError("unknown public dashboard")
    # Never forward cookies, Authorization, SQL, or arbitrary paths.
    with urllib.request.urlopen(f"{GRAFANA}/api/public/dashboards/{token}", timeout=15) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("dashboard too large")
    return json.loads(body)


def render_parameters(uid: str, panel: int, params: dict[str, str]) -> tuple[str, dict[str, str]]:
    if uid not in CATALOG or panel not in CATALOG[uid]["panels"]:
        raise ValueError("unknown saved panel")
    query = {"panelId": str(panel), "kiosk": "true", "refresh": ""}
    for key, default in (("from", "now-6h"), ("to", "now")):
        value = params.get(key, default)
        if not re.fullmatch(r"(?:now(?:[+-]\d{1,3}[mhdw])?|\d{13})", value):
            raise ValueError("invalid time")
        query[key] = value
    theme = params.get("theme", "light")
    if theme not in {"light", "dark"}:
        raise ValueError("invalid theme")
    query["theme"] = theme
    query["height"] = params.get("height", "400")
    width, height = int(params.get("width", "1000")), int(params.get("height", "400"))
    if not 320 <= width <= 1600 or not 80 <= height <= 1600:
        raise ValueError("invalid dimensions")
    token = CATALOG[uid]["token"]
    url = f"{GRAFANA}/public-dashboards/{token}?{urllib.parse.urlencode(query)}"
    return url, {"width": str(width), "height": str(height), "timeout": "45", "encoding": "png"}


def fetch_png(url: str, options: dict[str, str]) -> bytes:
    token = os.environ.get("GRAFANA_RENDERER_TOKEN", "")
    if not token:
        raise ValueError("renderer unavailable")
    request = urllib.request.Request(
        RENDERER + "?" + urllib.parse.urlencode({"url": url, **options}), headers={"X-Auth-Token": token}
    )
    with urllib.request.urlopen(request, timeout=50) as response:
        body = response.read(4 * 1024 * 1024 + 1)
    if not body.startswith(b"\x89PNG\r\n\x1a\n") or len(body) > 4 * 1024 * 1024:
        raise ValueError("invalid renderer response")
    return body


class BoundedWork:
    """Reject excess work; cancellation cannot release a running worker slot."""

    def __init__(self, limit: int):
        self.limit = limit
        self.pending: set[asyncio.Task] = set()

    async def run(self, function, *args):
        if len(self.pending) >= self.limit:
            raise OverflowError("busy")
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.pending.add(task)

        def done(completed):
            self.pending.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)


METADATA_WORK = BoundedWork(8)
RENDER_WORK = BoundedWork(2)
