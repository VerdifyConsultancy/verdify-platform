"""Actual collector projection SQL, using a private synthetic PostgreSQL fixture."""

import asyncio

from test_experiment_v2_proof_packet import collector
from test_public_band_lineage import as_json, baseline
from test_public_band_lineage import isolated_pg as isolated_pg


def test_collector_executes_current_target_and_climate_sql(isolated_pg, monkeypatch):
    q = isolated_pg
    q(baseline())
    # Migration 181 adds targets; do not mistake migration 171 for the current shape.
    assert q("SELECT b.temp_target FROM public.fn_band_setpoints(now()) b") == "80"
    additions = []
    for axis in ("temp", "rh", "vpd"):
        for zone in ("north", "south", "east", "west"):
            additions.append(f"ADD COLUMN {axis}_{zone} float8")
    additions.append("ADD COLUMN rh_avg float8")
    q("ALTER TABLE public.climate " + ",".join(additions))
    q("ALTER TABLE public.diagnostics ADD COLUMN active_probe_count integer, ADD COLUMN probe_health text")
    q("UPDATE public.climate SET house_temp_target_f=NULL, house_vpd_target=NULL WHERE ts='2026-09-04T21:03Z'")

    class Connection:
        async def fetchrow(self, sql, *args):
            if "house_temp_target_f" in sql:
                return as_json(q, sql)[0]
            return {}

        async def fetch(self, sql, *args):
            return as_json(q, sql) if "temp_north" in sql else []

        async def close(self):
            pass

    async def connect(*args):
        return Connection()

    monkeypatch.setattr(collector, "_connect", connect)
    db = asyncio.run(collector.collect_db("45039c86-c1d9-52f6-a0a9-d94a17bc4b14", mode="recovery"))
    assert db["targets"]["ts"].startswith("2026-09-04T21:03:")
    assert db["targets"]["house_temp_target_f"] is None
    assert db["targets"]["reconstructed_temp_target"] == 80
    assert db["targets"]["reconstructed_vpd_target"] == 1.2
    assert len(db["climate"]) == 2
    assert all(row["active_probe_count"] is None for row in db["climate"])
    assert db["climate"][-1]["ts"] == db["targets"]["ts"]
