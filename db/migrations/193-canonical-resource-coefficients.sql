-- 193-canonical-resource-coefficients.sql
--
-- Issue #437: converge runtime resource accounting on the canonical equipment
-- registry.  Historical values remain queryable, while the selected modeling
-- coefficient carries a revision, provenance, evidence reference, validity
-- interval, and uncertainty bounds.
--
-- Non-self-transactional: safe for an outer rollback proof.

CREATE TABLE IF NOT EXISTS public.equipment_aliases (
    id bigserial PRIMARY KEY,
    greenhouse_id text NOT NULL REFERENCES public.greenhouses(id),
    alias_slug text NOT NULL CHECK (alias_slug ~ '^[a-z][a-z0-9_]*$'),
    equipment_id integer NOT NULL REFERENCES public.equipment(id) ON DELETE CASCADE,
    alias_kind text NOT NULL DEFAULT 'telemetry'
        CHECK (alias_kind IN ('telemetry', 'legacy_catalog', 'display')),
    source text NOT NULL,
    evidence_ref text NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (greenhouse_id, alias_slug),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

COMMENT ON TABLE public.equipment_aliases IS
'Explicit compatibility aliases into canonical equipment. Active telemetry consumers resolve through v_equipment_alias_resolution; aliases never create a second physical asset.';

CREATE OR REPLACE FUNCTION public.reject_active_equipment_alias_collision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.valid_to IS NULL AND EXISTS (
        SELECT 1
        FROM public.equipment e
        WHERE e.greenhouse_id = NEW.greenhouse_id
          AND e.slug = NEW.alias_slug
          AND e.is_active
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'equipment alias %s/%s collides with an active canonical slug',
                NEW.greenhouse_id,
                NEW.alias_slug
            );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS reject_active_equipment_alias_collision
    ON public.equipment_aliases;
CREATE TRIGGER reject_active_equipment_alias_collision
BEFORE INSERT OR UPDATE OF greenhouse_id, alias_slug, valid_to
ON public.equipment_aliases
FOR EACH ROW
EXECUTE FUNCTION public.reject_active_equipment_alias_collision();

CREATE OR REPLACE FUNCTION public.reject_equipment_slug_alias_collision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.is_active AND EXISTS (
        SELECT 1
        FROM public.equipment_aliases a
        WHERE a.greenhouse_id = NEW.greenhouse_id
          AND a.alias_slug = NEW.slug
          AND a.valid_to IS NULL
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'active equipment slug %s/%s collides with a current alias',
                NEW.greenhouse_id,
                NEW.slug
            );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS reject_equipment_slug_alias_collision
    ON public.equipment;
CREATE TRIGGER reject_equipment_slug_alias_collision
BEFORE INSERT OR UPDATE OF greenhouse_id, slug, is_active
ON public.equipment
FOR EACH ROW
EXECUTE FUNCTION public.reject_equipment_slug_alias_collision();

CREATE TABLE IF NOT EXISTS public.resource_coefficients (
    id bigserial PRIMARY KEY,
    equipment_id integer NOT NULL REFERENCES public.equipment(id) ON DELETE CASCADE,
    resource_kind text NOT NULL
        CHECK (resource_kind IN ('electric_watts', 'gas_btu_per_hour', 'water_gpm')),
    unit text NOT NULL CHECK (unit IN ('W', 'BTU/h', 'gal/min')),
    nominal_value double precision NOT NULL CHECK (nominal_value >= 0),
    lower_bound double precision NOT NULL CHECK (lower_bound >= 0),
    upper_bound double precision NOT NULL CHECK (upper_bound >= 0),
    coefficient_source text NOT NULL
        CHECK (coefficient_source IN ('nameplate', 'operator', 'meter_fit', 'measured', 'unknown')),
    revision text NOT NULL CHECK (revision <> ''),
    evidence_ref text NOT NULL CHECK (evidence_ref <> ''),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    is_model_default boolean NOT NULL DEFAULT false,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (equipment_id, resource_kind, revision),
    CHECK (lower_bound <= nominal_value AND nominal_value <= upper_bound),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_resource_coefficients_active_default
    ON public.resource_coefficients (equipment_id, resource_kind)
    WHERE is_model_default AND valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_resource_coefficients_validity
    ON public.resource_coefficients (equipment_id, resource_kind, valid_from DESC);

COMMENT ON TABLE public.resource_coefficients IS
'Revisioned resource coefficients. Point estimates without provenance are prohibited; uncertainty bounds and evidence remain queryable even when a different revision is selected for modeling.';

-- Live lighting telemetry uses these names.  The older gl1/gl2/grow_light rows
-- remain as inactive aliases so historical links and maintenance text survive.
INSERT INTO public.equipment (
    greenhouse_id, slug, zone_id, kind, name, model, manufacturer, watts,
    cost_per_hour_usd, specs, install_date, is_active, notes
)
SELECT
    'vallery', 'grow_light_main', e.zone_id, 'light',
    'Main Lighting Circuit (4FT)', COALESCE(e.model, 'Barrina T8 42W'),
    COALESCE(e.manufacturer, 'Barrina'), COALESCE(e.watts, 630),
    e.cost_per_hour_usd,
    COALESCE(e.specs, '{}'::jsonb) || jsonb_build_object('telemetry_slug', 'grow_light_main'),
    e.install_date, true,
    'Canonical live telemetry identity; legacy alias gl1 retained by migration 193.'
FROM public.equipment e
WHERE e.greenhouse_id = 'vallery' AND e.slug = 'gl1'
ON CONFLICT (greenhouse_id, slug) DO UPDATE
SET is_active = true,
    name = EXCLUDED.name,
    model = COALESCE(public.equipment.model, EXCLUDED.model),
    manufacturer = COALESCE(public.equipment.manufacturer, EXCLUDED.manufacturer),
    watts = COALESCE(public.equipment.watts, EXCLUDED.watts),
    specs = public.equipment.specs || EXCLUDED.specs;

INSERT INTO public.equipment (
    greenhouse_id, slug, zone_id, kind, name, model, manufacturer, watts,
    cost_per_hour_usd, specs, install_date, is_active, notes
)
SELECT
    'vallery', 'grow_light_grow', e.zone_id, 'light',
    'Grow Lighting Circuit (2FT)', COALESCE(e.model, 'Barrina T8 24W'),
    COALESCE(e.manufacturer, 'Barrina'), COALESCE(e.watts, 816),
    e.cost_per_hour_usd,
    COALESCE(e.specs, '{}'::jsonb) || jsonb_build_object('telemetry_slug', 'grow_light_grow'),
    e.install_date, true,
    'Canonical live telemetry identity; legacy aliases gl2 and grow_light retained by migration 193.'
FROM public.equipment e
WHERE e.greenhouse_id = 'vallery' AND e.slug = 'grow_light'
ON CONFLICT (greenhouse_id, slug) DO UPDATE
SET is_active = true,
    name = EXCLUDED.name,
    model = COALESCE(public.equipment.model, EXCLUDED.model),
    manufacturer = COALESCE(public.equipment.manufacturer, EXCLUDED.manufacturer),
    watts = COALESCE(public.equipment.watts, EXCLUDED.watts),
    specs = public.equipment.specs || EXCLUDED.specs;

UPDATE public.equipment
SET is_active = false,
    notes = concat_ws(' ', notes, 'Inactive compatibility identity; resolve through equipment_aliases.')
WHERE greenhouse_id = 'vallery'
  AND slug IN ('gl1', 'gl2', 'grow_light');

INSERT INTO public.equipment_aliases (
    greenhouse_id, alias_slug, equipment_id, alias_kind, source, evidence_ref,
    valid_from
)
SELECT 'vallery', a.alias_slug, e.id, 'legacy_catalog',
       'migration_193', 'issue:#437', '2026-07-09 00:00:00-06'::timestamptz
FROM (VALUES
    ('gl1'::text, 'grow_light_main'::text),
    ('gl2'::text, 'grow_light_grow'::text),
    ('grow_light'::text, 'grow_light_grow'::text)
) AS a(alias_slug, canonical_slug)
JOIN public.equipment e
  ON e.greenhouse_id = 'vallery' AND e.slug = a.canonical_slug
ON CONFLICT (greenhouse_id, alias_slug) DO UPDATE
SET equipment_id = EXCLUDED.equipment_id,
    alias_kind = EXCLUDED.alias_kind,
    source = EXCLUDED.source,
    evidence_ref = EXCLUDED.evidence_ref,
    valid_to = NULL;

-- Preserve the previous unlabeled catalog points as historical evidence.
INSERT INTO public.resource_coefficients (
    equipment_id, resource_kind, unit, nominal_value, lower_bound, upper_bound,
    coefficient_source, revision, evidence_ref, valid_from, valid_to,
    is_model_default, notes
)
SELECT e.id, x.resource_kind, x.unit, x.nominal, x.nominal, x.nominal,
       'operator', 'legacy_catalog_085', 'migration:085,migration:020',
       '2026-01-01 00:00:00-07'::timestamptz,
       '2026-07-09 00:00:00-06'::timestamptz, false,
       'Historical point retained for comparison; provenance before migration 193 was not machine-readable.'
FROM (VALUES
    ('heat1'::text, 'electric_watts'::text, 'W'::text, 1500.0),
    ('fan1', 'electric_watts', 'W', 52.0),
    ('fan2', 'electric_watts', 'W', 52.0),
    ('fog', 'electric_watts', 'W', 1644.0),
    ('vent', 'electric_watts', 'W', 10.0),
    ('grow_light_main', 'electric_watts', 'W', 630.0),
    ('grow_light_grow', 'electric_watts', 'W', 816.0),
    ('heat2', 'gas_btu_per_hour', 'BTU/h', 75000.0)
) AS x(slug, resource_kind, unit, nominal)
JOIN public.equipment e
  ON e.greenhouse_id = 'vallery' AND e.slug = x.slug
ON CONFLICT (equipment_id, resource_kind, revision) DO UPDATE
SET nominal_value = EXCLUDED.nominal_value,
    lower_bound = EXCLUDED.lower_bound,
    upper_bound = EXCLUDED.upper_bound,
    coefficient_source = EXCLUDED.coefficient_source,
    evidence_ref = EXCLUDED.evidence_ref,
    valid_from = EXCLUDED.valid_from,
    valid_to = EXCLUDED.valid_to,
    is_model_default = false,
    notes = EXCLUDED.notes;

-- Selected revisions.  Fan/fog/heat bounds are intentionally provisional:
-- they preserve the July meter-fit range rather than promoting one fitted
-- point to billing-grade truth.  Exact lighting/vent/gas values retain their
-- operator provenance.
INSERT INTO public.resource_coefficients (
    equipment_id, resource_kind, unit, nominal_value, lower_bound, upper_bound,
    coefficient_source, revision, evidence_ref, valid_from, valid_to,
    is_model_default, notes
)
SELECT e.id, x.resource_kind, x.unit, x.nominal, x.low, x.high,
       x.source, x.revision, x.evidence_ref,
       '2026-07-09 00:00:00-06'::timestamptz, NULL, true, x.notes
FROM (VALUES
    ('heat1'::text, 'electric_watts'::text, 'W'::text, 1436.0, 1350.0, 1500.0,
     'meter_fit'::text, 'meter_fit_2026_07_09'::text, 'issue:#437,july-9-meter-audit'::text,
     'Provisional bounded fit; the historical 1500 W catalog point remains queryable.'::text),
    ('fan1', 'electric_watts', 'W', 113.0, 102.0, 124.0,
     'meter_fit', 'meter_fit_2026_07_09', 'issue:#437,july-9-meter-audit',
     'Per-fan bounded fit; not a billing-grade measurement.'),
    ('fan2', 'electric_watts', 'W', 113.0, 102.0, 124.0,
     'meter_fit', 'meter_fit_2026_07_09', 'issue:#437,july-9-meter-audit',
     'Per-fan bounded fit; not a billing-grade measurement.'),
    ('fog', 'electric_watts', 'W', 468.0, 315.0, 620.0,
     'meter_fit', 'meter_fit_2026_07_09', 'issue:#437,july-9-meter-audit',
     'Wide overlap-sensitive fit; the historical 1644 W catalog point remains queryable.'),
    ('vent', 'electric_watts', 'W', 10.0, 8.0, 12.0,
     'operator', 'operator_catalog_2026_07_09', 'migration:085',
     'Operator catalog point with provisional +/-20% bounds; no circuit-isolated measurement.'),
    ('grow_light_main', 'electric_watts', 'W', 630.0, 567.0, 693.0,
     'operator', 'operator_fixture_count_2026_07_09', 'vault:equipment.md',
     '15 fixtures times a 42 W catalog point; provisional +/-10% bounds until circuit-isolated measurement.'),
    ('grow_light_grow', 'electric_watts', 'W', 816.0, 734.4, 897.6,
     'operator', 'operator_fixture_count_2026_07_09', 'vault:equipment.md',
     '34 fixtures times a 24 W catalog point; provisional +/-10% bounds until circuit-isolated measurement.'),
    ('heat2', 'gas_btu_per_hour', 'BTU/h', 75000.0, 75000.0, 75000.0,
     'nameplate', 'nameplate_2026_07_09', 'model:Lennox-LF24-75A-5',
     'Nameplate input rating; gas flow is not metered.')
) AS x(slug, resource_kind, unit, nominal, low, high, source, revision, evidence_ref, notes)
JOIN public.equipment e
  ON e.greenhouse_id = 'vallery' AND e.slug = x.slug
ON CONFLICT (equipment_id, resource_kind, revision) DO UPDATE
SET nominal_value = EXCLUDED.nominal_value,
    lower_bound = EXCLUDED.lower_bound,
    upper_bound = EXCLUDED.upper_bound,
    coefficient_source = EXCLUDED.coefficient_source,
    evidence_ref = EXCLUDED.evidence_ref,
    valid_from = EXCLUDED.valid_from,
    valid_to = NULL,
    is_model_default = true,
    notes = EXCLUDED.notes;

CREATE OR REPLACE VIEW public.v_equipment_alias_resolution AS
SELECT
    e.greenhouse_id,
    e.slug AS input_slug,
    e.id AS equipment_id,
    e.slug AS canonical_slug,
    e.kind,
    e.name,
    'canonical'::text AS resolution_kind,
    'equipment'::text AS source,
    e.is_active
FROM public.equipment e
WHERE e.is_active
UNION ALL
SELECT
    a.greenhouse_id,
    a.alias_slug AS input_slug,
    e.id AS equipment_id,
    e.slug AS canonical_slug,
    e.kind,
    e.name,
    a.alias_kind AS resolution_kind,
    a.source,
    e.is_active
FROM public.equipment_aliases a
JOIN public.equipment e ON e.id = a.equipment_id
WHERE a.valid_to IS NULL
  AND e.is_active
  AND NOT EXISTS (
      SELECT 1
      FROM public.equipment canonical
      WHERE canonical.greenhouse_id = a.greenhouse_id
        AND canonical.slug = a.alias_slug
        AND canonical.is_active
  );

COMMENT ON VIEW public.v_equipment_alias_resolution IS
'One-row resolution of canonical and historical telemetry slugs into active canonical equipment. Cross-table triggers reject a current alias that collides with an active canonical slug; the view also gives canonical rows defensive precedence.';

CREATE OR REPLACE VIEW public.v_equipment_resource_catalog AS
SELECT
    e.greenhouse_id,
    e.id AS equipment_id,
    e.slug AS equipment_slug,
    e.kind AS equipment_kind,
    e.name AS equipment_name,
    c.resource_kind,
    c.unit,
    c.nominal_value AS coefficient_nominal,
    c.lower_bound AS coefficient_low,
    c.upper_bound AS coefficient_high,
    c.coefficient_source,
    c.revision AS coefficient_revision,
    c.evidence_ref,
    c.valid_from,
    c.valid_to,
    c.lower_bound <> c.upper_bound AS has_uncertainty,
    COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'revision', h.revision,
            'source', h.coefficient_source,
            'nominal', h.nominal_value,
            'low', h.lower_bound,
            'high', h.upper_bound,
            'unit', h.unit,
            'evidence_ref', h.evidence_ref,
            'valid_from', h.valid_from,
            'valid_to', h.valid_to
        ) ORDER BY h.valid_from, h.revision)
        FROM public.resource_coefficients h
        WHERE h.equipment_id = c.equipment_id
          AND h.resource_kind = c.resource_kind
          AND h.id <> c.id
    ), '[]'::jsonb) AS alternative_revisions
FROM public.resource_coefficients c
JOIN public.equipment e ON e.id = c.equipment_id
WHERE c.is_model_default
  AND c.valid_to IS NULL
  AND e.is_active;

COMMENT ON VIEW public.v_equipment_resource_catalog IS
'Selected canonical modeling coefficient plus bounds, provenance, revision, evidence, and all alternative historical revisions. Consumers must retain these labels.';

CREATE OR REPLACE VIEW public.v_equipment_assets_compat AS
SELECT
    e.id,
    e.slug AS equipment,
    e.name AS description,
    e.model,
    NULL::text AS serial_no,
    e.install_date,
    NULL::date AS warranty_exp,
    electric.coefficient_nominal AS wattage,
    gas.coefficient_nominal AS btu_rating,
    concat_ws(' ', e.notes, 'Canonical compatibility projection; maintenance_log remains on legacy equipment_assets.') AS notes
FROM public.equipment e
LEFT JOIN public.v_equipment_resource_catalog electric
  ON electric.equipment_id = e.id AND electric.resource_kind = 'electric_watts'
LEFT JOIN public.v_equipment_resource_catalog gas
  ON gas.equipment_id = e.id AND gas.resource_kind = 'gas_btu_per_hour'
WHERE e.is_active;

COMMENT ON VIEW public.v_equipment_assets_compat IS
'Read compatibility projection from canonical equipment/resource coefficients. The legacy table remains only to preserve maintenance_log foreign keys during convergence.';
