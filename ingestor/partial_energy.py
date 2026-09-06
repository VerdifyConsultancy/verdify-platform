"""Propagate partial-meter missingness into the mutable daily summary."""

REFRESH_SQL = """
UPDATE public.daily_summary ds
   SET (kwh_total,peak_kw) = (
       SELECT ed.measured_kwh::double precision,(ed.peak_watts/1000.0)::double precision
       FROM public.v_energy_daily ed WHERE ed.date=ds.date AND ed.greenhouse_id=ds.greenhouse_id
   ), captured_at=now()
 WHERE ds.date=$1 AND ds.greenhouse_id=$2
"""


async def refresh_partial_energy(conn, target_day, greenhouse_id):
    # A missing row or NULL measurement must clear stale derived values.
    # Do not touch frozen outcomes, source history or another greenhouse.
    await conn.execute(REFRESH_SQL, target_day, greenhouse_id)
