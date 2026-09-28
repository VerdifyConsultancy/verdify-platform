-- Prospective route identity for the separate passive winter packet.
-- Review the exact source bytes before running. This SQL creates no physical
-- sensor identity, prior validity, target, study instance, or device setting.
-- Source: route-only-panel.json
-- SHA-256: a570eac0f076267af8d6a8b56db246972aeb71319e1041e3d8301715382ecc6f
-- The candidate's firmware/greenhouse/hardware.yaml,
-- firmware/greenhouse/sensors.yaml, and
-- ingestor/entity_map.py SHA-256 entries matched branch source on 2026-09-27.
BEGIN;
LOCK TABLE public.fixed_panel_contributor_revisions IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.fixed_panel_contributor_revisions
         WHERE greenhouse_id = 'vallery'
           AND zone IN ('north', 'east', 'west')
           AND valid_from < '2027-01-01T07:00:00Z'::timestamptz
           AND valid_to > '2026-11-02T13:00:00Z'::timestamptz
    ) THEN
        RAISE EXCEPTION 'prospective route interval already has a declaration';
    END IF;
END;
$preflight$;
INSERT INTO public.fixed_panel_contributor_revisions
    (greenhouse_id, zone, route_id, modbus_address, temp_field, vpd_field,
     physical_serial, physical_evidence_sha256, source_revision_sha256,
     valid_from, valid_to, capture_scope)
VALUES
    ('vallery', 'north', 'north_wall_probe', 2, 'temp_north', 'vpd_north',
     NULL, NULL, 'a570eac0f076267af8d6a8b56db246972aeb71319e1041e3d8301715382ecc6f',
     '2026-11-02T13:00:00Z', '2027-01-01T07:00:00Z', 'route_only'),
    ('vallery', 'east', 'east_wall_probe', 5, 'temp_east', 'vpd_east',
     NULL, NULL, 'a570eac0f076267af8d6a8b56db246972aeb71319e1041e3d8301715382ecc6f',
     '2026-11-02T13:00:00Z', '2027-01-01T07:00:00Z', 'route_only'),
    ('vallery', 'west', 'west_wall_probe', 3, 'temp_west', 'vpd_west',
     NULL, NULL, 'a570eac0f076267af8d6a8b56db246972aeb71319e1041e3d8301715382ecc6f',
     '2026-11-02T13:00:00Z', '2027-01-01T07:00:00Z', 'route_only')
RETURNING revision_id, recorded_at, zone, route_id, capture_scope;
COMMIT;
