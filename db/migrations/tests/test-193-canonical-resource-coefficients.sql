-- Fixed catalog/provenance fixture for migration 193.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.greenhouses (
    id text PRIMARY KEY
);

CREATE TABLE public.equipment (
    id serial PRIMARY KEY,
    greenhouse_id text NOT NULL REFERENCES public.greenhouses(id),
    slug text NOT NULL,
    zone_id integer,
    kind text NOT NULL,
    name text NOT NULL,
    model text,
    manufacturer text,
    watts double precision,
    cost_per_hour_usd double precision,
    specs jsonb NOT NULL DEFAULT '{}'::jsonb,
    install_date date,
    is_active boolean NOT NULL DEFAULT true,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (greenhouse_id, slug)
);

INSERT INTO public.greenhouses(id) VALUES ('vallery');

INSERT INTO public.equipment (
    greenhouse_id, slug, kind, name, model, manufacturer, watts, specs
)
SELECT 'vallery', x.slug, x.kind, initcap(replace(x.slug, '_', ' ')),
       x.model, x.manufacturer, x.watts, '{}'::jsonb
FROM (VALUES
    ('heat1'::text, 'heater'::text, 'YE 1500W'::text, NULL::text, 1500.0),
    ('heat2', 'heater', 'Lennox LF24-75A-5', 'Lennox', NULL),
    ('fan1', 'fan', 'KEN BROWN 18 inch', 'KEN BROWN', 52.0),
    ('fan2', 'fan', 'KEN BROWN 18 inch', 'KEN BROWN', 52.0),
    ('vent', 'vent', 'actuator', NULL, 10.0),
    ('fog', 'fog', 'AquaFog XE 2000', 'AquaFog', 1644.0),
    ('mister_south', 'mister', NULL, NULL, NULL),
    ('mister_west', 'mister', NULL, NULL, NULL),
    ('mister_center', 'mister', NULL, NULL, NULL),
    ('drip_wall', 'drip', NULL, NULL, NULL),
    ('drip_center', 'drip', NULL, NULL, NULL),
    ('fert_master_valve', 'valve', NULL, NULL, NULL),
    ('mister_south_fert', 'valve', NULL, NULL, NULL),
    ('mister_west_fert', 'valve', NULL, NULL, NULL),
    ('drip_wall_fert', 'valve', NULL, NULL, NULL),
    ('drip_center_fert', 'valve', NULL, NULL, NULL),
    ('gl1', 'light', 'Barrina T8 42W', 'Barrina', 630.0),
    ('gl2', 'light', NULL, NULL, NULL),
    ('grow_light', 'light', 'Barrina T8 24W', 'Barrina', 816.0)
) AS x(slug, kind, model, manufacturer, watts);

\i db/migrations/193-canonical-resource-coefficients.sql
-- Rerun proves additive migration idempotence.
\i db/migrations/193-canonical-resource-coefficients.sql

-- Independent captured telemetry inventory; this is not generated from the
-- catalog under test. Only physical control-output slugs must resolve as assets.
\i db/migrations/tests/fixtures/active-equipment-state-inventory-2026-07-09.sql

DO $$
DECLARE
    target_id integer;
BEGIN
    SELECT id INTO target_id
    FROM public.equipment
    WHERE greenhouse_id = 'vallery' AND slug = 'fan1';

    BEGIN
        INSERT INTO public.equipment_aliases (
            greenhouse_id, alias_slug, equipment_id, source, evidence_ref
        ) VALUES (
            'vallery', 'heat1', target_id, 'fixture', 'test:alias-collision'
        );
        RAISE EXCEPTION 'alias/canonical collision was accepted';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

DO $$
DECLARE
    unresolved integer;
    multiply_resolved integer;
    active_lights integer;
    selected_coefficients integer;
    provenance_failures integer;
    bounded_conflicts integer;
    compatibility_lights integer;
    exact_legacy_points integer;
    captured_outputs integer;
    captured_inventory integer;
BEGIN
    WITH expected AS (
        SELECT equipment AS slug
        FROM captured_equipment_state_inventory
        WHERE telemetry_role = 'physical_control_output'
    )
    SELECT count(*) INTO unresolved
    FROM expected x
    LEFT JOIN public.v_equipment_alias_resolution r
      ON r.greenhouse_id = 'vallery' AND r.input_slug = x.slug
    WHERE r.equipment_id IS NULL;

    SELECT count(*) INTO multiply_resolved
    FROM (
        SELECT input_slug
        FROM public.v_equipment_alias_resolution
        WHERE greenhouse_id = 'vallery'
        GROUP BY input_slug
        HAVING count(*) <> 1
    ) x;

    SELECT count(*) INTO active_lights
    FROM public.equipment
    WHERE greenhouse_id = 'vallery'
      AND slug IN ('grow_light_main', 'grow_light_grow')
      AND is_active;

    SELECT count(*) INTO selected_coefficients
    FROM public.v_equipment_resource_catalog
    WHERE greenhouse_id = 'vallery';

    SELECT count(*) INTO provenance_failures
    FROM public.resource_coefficients
    WHERE coefficient_source NOT IN ('nameplate', 'operator', 'meter_fit', 'measured', 'unknown')
       OR revision = '' OR evidence_ref = ''
       OR lower_bound > nominal_value OR nominal_value > upper_bound;

    SELECT count(*) INTO bounded_conflicts
    FROM public.v_equipment_resource_catalog
    WHERE equipment_slug IN ('heat1', 'fan1', 'fan2', 'fog')
      AND coefficient_source = 'meter_fit'
      AND has_uncertainty
      AND jsonb_array_length(alternative_revisions) >= 1;

    SELECT count(*) INTO compatibility_lights
    FROM public.v_equipment_assets_compat
    WHERE equipment IN ('grow_light_main', 'grow_light_grow')
      AND wattage IS NOT NULL;

    SELECT count(*) INTO exact_legacy_points
    FROM public.resource_coefficients
    WHERE revision = 'legacy_catalog_085'
      AND lower_bound = upper_bound;

    SELECT count(*) FILTER (WHERE telemetry_role = 'physical_control_output'),
           count(*)
      INTO captured_outputs, captured_inventory
    FROM captured_equipment_state_inventory;

    IF unresolved <> 0 OR multiply_resolved <> 0 THEN
        RAISE EXCEPTION 'alias convergence failed: unresolved %, multiply resolved %',
            unresolved, multiply_resolved;
    END IF;
    IF active_lights <> 2 OR compatibility_lights <> 2 THEN
        RAISE EXCEPTION 'live lighting identity failed: canonical %, compatibility %',
            active_lights, compatibility_lights;
    END IF;
    IF selected_coefficients <> 8 OR provenance_failures <> 0 THEN
        RAISE EXCEPTION 'coefficient contract failed: selected %, provenance failures %',
            selected_coefficients, provenance_failures;
    END IF;
    IF bounded_conflicts <> 4 THEN
        RAISE EXCEPTION 'provisional fan/fog/heat evidence not preserved: %', bounded_conflicts;
    END IF;
    IF exact_legacy_points <> 0 THEN
        RAISE EXCEPTION 'unproven legacy coefficients remained point-exact: %',
            exact_legacy_points;
    END IF;
    IF captured_outputs <> 18 OR captured_inventory <> 39 THEN
        RAISE EXCEPTION 'captured telemetry inventory drifted: outputs %, total %',
            captured_outputs, captured_inventory;
    END IF;
    IF (SELECT canonical_slug FROM public.v_equipment_alias_resolution
        WHERE greenhouse_id = 'vallery' AND input_slug = 'gl1') <> 'grow_light_main'
       OR (SELECT canonical_slug FROM public.v_equipment_alias_resolution
        WHERE greenhouse_id = 'vallery' AND input_slug = 'grow_light') <> 'grow_light_grow' THEN
        RAISE EXCEPTION 'legacy lighting alias mapped to wrong physical circuit';
    END IF;
END $$;

SELECT equipment_slug, resource_kind, coefficient_low, coefficient_nominal,
       coefficient_high, coefficient_source, coefficient_revision,
       jsonb_array_length(alternative_revisions) AS alternative_revisions
FROM public.v_equipment_resource_catalog
ORDER BY equipment_slug, resource_kind;

ROLLBACK;
