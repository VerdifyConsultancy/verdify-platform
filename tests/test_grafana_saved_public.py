import asyncio
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from verdify_public.grafana_public import CATALOG, BoundedWork, panel_metadata, public_panel, render_parameters


def test_exactly_approved_catalog_and_source_panels():
    root = Path(__file__).resolve().parents[1]
    assert len(CATALOG) == 20
    assert CATALOG == json.loads((root / "site/quartz/plugins/transformers/grafana-public-shares.json").read_text())
    for uid, row in CATALOG.items():
        dashboard = json.loads((root / f"grafana/dashboards/{uid}.json").read_text())
        assert dashboard["uid"] == uid
        assert not dashboard.get("templating", {}).get("list")

        def ids(panels):
            return {p["id"] for p in panels if p.get("type") != "row"} | {
                i for p in panels for i in ids(p.get("panels", []))
            }

        assert set(row["panels"]) == ids(dashboard["panels"])


def test_metadata_filters_copy_only_and_native_query_contract_stays_unchanged():
    original = {"dashboard": {"panels": [{"id": 2, "targets": [{"rawSql": "SAVED SQL"}], "gridPos": {}}, {"id": 3}]}}
    filtered = panel_metadata(original, 2)
    assert len(filtered["dashboard"]["panels"]) == 1
    assert filtered["dashboard"]["panels"][0]["targets"] == original["dashboard"]["panels"][0]["targets"]
    assert len(original["dashboard"]["panels"]) == 2
    with pytest.raises(ValueError):
        panel_metadata(original, 999)


def test_referrer_is_only_a_saved_panel_selection_not_an_authentication_claim():
    row = CATALOG["site-climate"]
    token, panel = row["token"], row["panels"][0]
    assert public_panel(token, f"https://graphs.verdify.ai/public-dashboards/{token}?panelId={panel}") == panel
    assert public_panel(token, f"https://evil.example/public-dashboards/{token}?panelId={panel}") is None
    with pytest.raises(ValueError):
        public_panel(token, f"https://graphs.verdify.ai/public-dashboards/{token}?panelId=999999")
    with pytest.raises(ValueError):
        public_panel("unknown", "")


@pytest.mark.parametrize(
    "params", [{"width": "99999"}, {"height": "-1"}, {"from": "now;select"}, {"theme": "https://evil"}]
)
def test_renderer_rejects_unbounded_or_injected_parameters(params):
    with pytest.raises(ValueError):
        render_parameters("site-climate", CATALOG["site-climate"]["panels"][0], params)


def test_renderer_target_is_fixed_and_caller_urls_or_credentials_are_ignored():
    panel = CATALOG["site-climate"]["panels"][0]
    url, options = render_parameters("site-climate", panel, {"url": "http://evil", "renderKey": "bad", "width": "800"})
    assert urlsplit(url).netloc == "graphs.verdify.ai"
    assert parse_qs(urlsplit(url).query)["panelId"] == [str(panel)]
    assert "evil" not in url and "bad" not in url
    assert options["width"] == "800"
    with pytest.raises(ValueError):
        render_parameters("private-dashboard", panel, {})


def test_cancellation_cannot_release_worker_capacity():
    async def run():
        started, release = threading.Event(), threading.Event()

        def work():
            started.set()
            assert release.wait(2)

        bounded = BoundedWork(1)
        task = asyncio.create_task(bounded.run(work))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(OverflowError):
            await bounded.run(lambda: None)
        release.set()
        await asyncio.gather(*bounded.pending)
        await asyncio.sleep(0)
        assert not bounded.pending

    asyncio.run(run())


@pytest.mark.parametrize("value", ["now-6M/M", "now/M", "now-1y/y", "now-30d/d", "now+72h"])
def test_saved_calendar_date_math_is_preserved(value):
    panel = CATALOG["site-evidence-economics"]["panels"][0]
    url, _ = render_parameters("site-evidence-economics", panel, {"from": value})
    assert parse_qs(urlsplit(url).query)["from"] == [value]


@pytest.mark.parametrize("value", ["now-6M/M?url=evil", "now/d/../../etc", "now-1y;SELECT"])
def test_calendar_date_math_cannot_carry_paths_or_queries(value):
    with pytest.raises(ValueError):
        render_parameters("site-home", CATALOG["site-home"]["panels"][0], {"from": value})
