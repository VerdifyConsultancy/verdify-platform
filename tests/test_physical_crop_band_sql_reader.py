"""Private PostgreSQL publication → actual API/MCP consumers → render proof."""

from __future__ import annotations

import ast
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_physical_crop_band_evidence import DAY, _publisher, qualified_row
from test_scorecard_semantics import isolated_pg  # noqa: F401

from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.physical_crop_band import PhysicalCropBandEvidence

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/274-qualified-physical-crop-band-publication.sql"


def extract(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
    if name != "read_physical_crop_band_evidence":
        node.args.defaults = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / path), "exec"), namespace)  # noqa: S102
    return namespace[name]


def qualification(d):
    return {
        "contract": "trusted-owner-physical-qualification-v1",
        "attested_by": "verdify",
        "authenticity_statement": "SYNTHETIC PRIVATE FIXTURE ONLY: never production physical evidence.",
        "artifact_sha256": "f" * 64,
        "input_hashes": {
            k: d[k]
            for k in ("target_manifest_sha256", "panel_manifest_sha256", "input_sha256", "calculation_source_sha256")
        },
    }


def test_private_sql_actual_transport_and_planner_public_render(isolated_pg):  # noqa: F811
    query = isolated_pg
    query(
        "CREATE ROLE verdify LOGIN; CREATE ROLE verdify_api_runtime; CREATE ROLE verdify_mcp_runtime; "
        "CREATE ROLE verdify_api_runtime_login LOGIN IN ROLE verdify_api_runtime; "
        "CREATE ROLE verdify_mcp_runtime_login LOGIN IN ROLE verdify_mcp_runtime; "
        "CREATE ROLE unauthorized LOGIN; GRANT CREATE ON SCHEMA public TO verdify;"
    )
    sql = MIGRATION.read_text().split("END $preflight$;", 1)[1].split("DO $postflight$", 1)[0]
    query("SET ROLE verdify; " + sql + " RESET ROLE;")
    host = query("SHOW unix_socket_directories").strip()
    port = int(query("SHOW port"))
    diagnostic = qualified_row()["diagnostic"]
    namespaces = {}

    async def run():
        owner = await asyncpg.connect(user="verdify", database="postgres", host=host, port=port)
        await owner.execute(
            "CREATE FUNCTION public.fn_planner_scorecard(date) RETURNS TABLE(metric text,value numeric) LANGUAGE sql AS $$ SELECT * FROM (VALUES ('scorecard_contract_version',2.0),('compliance_pct',6.1),('compliance_v2_attributable_pct',85.8)) x $$"
        )
        for user in ("verdify_api_runtime_login", "verdify_mcp_runtime_login", "unauthorized"):
            conn = await asyncpg.connect(user=user, database="postgres", host=host, port=port)
            for command in (
                "SELECT * FROM public.physical_crop_band_revisions",
                "INSERT INTO public.physical_crop_band_revisions(day,greenhouse_id,diagnostic,qualification) VALUES(current_date,'vallery','{}','{}')",
                "SELECT public.fn_publish_physical_crop_band_evidence('{}','{}')",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(command)
            await conn.close()
        for mutate in ("physical_proof_eligible", "per_probe_freshness_verified", "historical_crop_target_verified"):
            bad = copy.deepcopy(diagnostic)
            bad[mutate] = False
            with pytest.raises(asyncpg.RaiseError):
                await owner.fetchval(
                    "SELECT public.fn_publish_physical_crop_band_evidence($1::jsonb,$2::jsonb)",
                    json.dumps(bad),
                    json.dumps(qualification(bad)),
                )
        bad = copy.deepcopy(diagnostic)
        bad["joint"]["in_band_pct"] = 85.8
        with pytest.raises(asyncpg.RaiseError):
            await owner.fetchval(
                "SELECT public.fn_publish_physical_crop_band_evidence($1::jsonb,$2::jsonb)",
                json.dumps(bad),
                json.dumps(qualification(bad)),
            )
        second = copy.deepcopy(diagnostic)
        second["target_version"] += "-reviewed-v2"
        for d in (diagnostic, second):
            await owner.fetchval(
                "SELECT public.fn_publish_physical_crop_band_evidence($1::jsonb,$2::jsonb)",
                json.dumps(d),
                json.dumps(qualification(d)),
            )
        assert await owner.fetchval("SELECT count(*) FROM public.physical_crop_band_revisions") == 2
        assert (
            await owner.fetchval(
                "SELECT diagnostic->>'target_version' FROM public.physical_crop_band_revisions WHERE revision_id=1"
            )
            == diagnostic["target_version"]
        )
        for key in qualification(diagnostic):
            invalid_q = qualification(diagnostic)
            del invalid_q[key]
            with pytest.raises(asyncpg.RaiseError):
                await owner.fetchval(
                    "SELECT public.fn_publish_physical_crop_band_evidence($1::jsonb,$2::jsonb)",
                    json.dumps(diagnostic),
                    json.dumps(invalid_q),
                )
        from datetime import UTC, date, datetime, time
        from zoneinfo import ZoneInfo

        future = copy.deepcopy(diagnostic)
        future["day"] = "2050-09-25"
        future["window_start"] = (
            datetime.combine(date(2050, 9, 25), time(), ZoneInfo("America/Denver")).astimezone(UTC).isoformat()
        )
        future["window_end"] = (
            datetime.combine(date(2050, 9, 26), time(), ZoneInfo("America/Denver")).astimezone(UTC).isoformat()
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.fetchval(
                "SELECT public.fn_publish_physical_crop_band_evidence($1::jsonb,$2::jsonb)",
                json.dumps(future),
                json.dumps(qualification(future)),
            )

        with pytest.raises(asyncpg.RaiseError):
            await owner.execute("DELETE FROM public.physical_crop_band_revisions")
        with pytest.raises(asyncpg.RaiseError):
            await owner.execute("UPDATE public.physical_crop_band_revisions SET diagnostic='{}'")
        await owner.close()
        for path, login in (
            ("api/main.py", "verdify_api_runtime_login"),
            ("mcp/server.py", "verdify_mcp_runtime_login"),
        ):
            conn = await asyncpg.connect(user=login, database="postgres", host=host, port=port)
            ns = {
                "asyncpg": asyncpg,
                "json": json,
                "ValidationError": ValidationError,
                "PhysicalCropBandEvidence": PhysicalCropBandEvidence,
            }
            reader = extract(path, "read_physical_crop_band_evidence", ns)
            physical = await reader(conn, DAY)
            assert physical.availability == "available" and physical.revision_id == 2
            missing = await reader(conn, DAY.replace(day=24))
            assert missing.availability == "unavailable" and missing.diagnostic is None
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM pg_catalog.pg_proc WHERE proname='fn_physical_crop_band_evidence'"
                )
                == 1
            )
            await conn.close()
            namespaces[path] = (ns, physical)
        return namespaces

    results = asyncio.run(run())

    # The actual API route executes its SQL on an actual private database connection.
    async def api_route(day, consumer="api"):
        conn = await asyncpg.connect(user="verdify_api_runtime_login", database="postgres", host=host, port=port)

        class Checkout:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *args):
                await conn.close()

        async def metrics(c, d):
            return await c.fetch("SELECT * FROM public.fn_planner_scorecard($1)", d)

        async def observed(c, d):
            from verdify_schemas.observed_minutes import ObservedMinuteEvidence

            return ObservedMinuteEvidence(day=d)

        async def route(c, d):
            from verdify_schemas.physical_crop_band import RouteOnlyCropBandEvidence

            return RouteOnlyCropBandEvidence(day=d)

        async def native(c, d):
            from verdify_schemas.fixed_panel_native_route import NativeFixedPanelRouteEvidence

            return NativeFixedPanelRouteEvidence(day=d)

        path = "api/main.py" if consumer == "api" else "mcp/server.py"

        async def db():
            return conn

        ns = dict(
            results[path][0],
            ScorecardResponse=ScorecardResponse,
            _fetch_planner_scorecard=metrics,
            _db=db,
            pool=SimpleNamespace(acquire=Checkout),
            read_observed_minute_evidence=observed,
            read_route_only_crop_band_evidence=route,
            read_native_fixed_panel_route_evidence=native,
        )
        if consumer == "api":
            return await extract(path, "planner_scorecard", ns)(day)
        from datetime import datetime

        ns["datetime"] = datetime
        from mcp.server.fastmcp import FastMCP

        server = FastMCP("private-physical-fixture")
        server.tool()(extract(path, "scorecard", ns))
        result = await server.call_tool("scorecard", {"target_date": day.isoformat()})
        return json.loads(result[0].text)

    app = FastAPI()

    @app.get("/api/v1/scorecard")
    async def transported():
        return await api_route(DAY)

    with TestClient(app) as client:
        response = client.get("/api/v1/scorecard")
    assert response.status_code == 200
    wire = response.json()
    assert wire["compliance_v2_attributable_pct"] == 85.8
    assert wire["physical_crop_band_evidence"]["diagnostic"]["joint"]["in_band_pct"] == pytest.approx(100 * 2 / 96)
    card = ScorecardResponse.model_validate({k: v for k, v in wire.items() if k != "metric_semantics"})
    rendered = _publisher().physical_crop_band_block(wire["physical_crop_band_evidence"], DAY.isoformat())
    assert "2.1% joint" in rendered and "2/96" in rendered and "85.8%" not in rendered
    assert card.climate_evidence()["graded_compliance_attributable_pct"] == 85.8
    mcp_wire = asyncio.run(api_route(DAY, "mcp"))
    assert mcp_wire["physical_crop_band_evidence"]["revision_id"] == 2
    assert mcp_wire["physical_crop_band_evidence"]["diagnostic"]["joint"]["in_band_pct"] == pytest.approx(100 * 2 / 96)
    assert mcp_wire["compliance_v2_attributable_pct"] == 85.8
    # Iris receives this exact registered scorecard tool JSON; public snapshot
    # rendering receives the same shared typed climate projection.
    import yaml

    iris = yaml.safe_load((ROOT / "hermes/iris/config.yaml").read_text())
    assert "scorecard" in iris["mcp_servers"]["verdify_greenhouse"]["tools"]["include"]
    climate = card.climate_evidence()
    assert (
        json.loads(json.dumps(mcp_wire["physical_crop_band_evidence"])) == wire["physical_crop_band_evidence"]
        or mcp_wire["physical_crop_band_evidence"]["diagnostic"] == wire["physical_crop_band_evidence"]["diagnostic"]
    )
    public_html = _publisher().planning_block({"planning_quality": climate, "generated_at": DAY.isoformat()})
    assert "85.8%" in public_html and "2.1% joint" in public_html


def test_reviewed_artifact_bytes_must_bind_all_diagnostic_hashes(tmp_path):
    import hashlib

    spec = importlib.util.spec_from_file_location(
        "physical_producer", ROOT / "scripts/publish-physical-crop-band-evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    diagnostic = copy.deepcopy(qualified_row()["diagnostic"])
    paths = {}
    for key in ("target_manifest_sha256", "panel_manifest_sha256", "input_sha256", "calculation_source_sha256"):
        path = tmp_path / key
        path.write_text("SYNTHETIC PRIVATE FIXTURE " + key)
        paths[key] = path
        diagnostic[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact = tmp_path / "diagnostic.json"
    artifact.write_text(json.dumps(diagnostic))
    payload, receipt = module.prepare(
        artifact, paths, "SYNTHETIC PRIVATE FIXTURE: reviewed independent artifact inputs."
    )
    assert receipt["input_hashes"] == {key: payload[key] for key in paths}
    assert receipt["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    paths["input_sha256"].write_text("changed input")
    with pytest.raises(ValueError, match="evidence bytes differ"):
        module.prepare(artifact, paths, "SYNTHETIC PRIVATE FIXTURE: reviewed independent artifact inputs.")
