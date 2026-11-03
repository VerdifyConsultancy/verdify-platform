"""API-20's one HTTP call: ``POST {SETPOINT_SERVER_URL}/lights/{light}/{state}`` (VD-57, #1170). No routes and no SQL.

``verdify-setpoint-server`` is the dashboard's one Lutron path (D-IRP-24): nothing else here calls Home Assistant, and
the lights never touch the ESP32. It takes no token; NetworkPolicy allow-setpoint-from-operator-api is its guard. It
answers 200 once Home Assistant saw Lutron reach the state, 502 when Home Assistant failed the call and 504 when Lutron
did not reach the state.

The 12 s cap keeps the answer inside the BFF proxy's 15 s. The server can take longer (up to 5 s in ``ha_call``, then up
to 8 s polling in ``ha_confirm_state``), so a light can change after this call has given up; the Lutron readback in
API-13 then shows what happened. The route calls this outside any database transaction.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from verdify_services.lights import Light, LightResult, LightState

log = logging.getLogger(__name__)
TIMEOUT_S = 12.0
_RESULTS: dict[int, LightResult] = {200: "confirmed", 502: "ha_failed", 504: "not_confirmed"}


async def switch(base_url: str, light: Light, state: LightState) -> LightResult:
    """The call's result. No answer in 12 s, no connection or an answer this contract does not know is ``timeout``:
    nothing says what the light did, so only the readback can. The log line alone tells those causes apart."""
    try:
        async with asyncio.timeout(TIMEOUT_S), httpx.AsyncClient(timeout=TIMEOUT_S, trust_env=False) as client:
            response = await client.post(f"{base_url.rstrip('/')}/lights/{light}/{state}")
    except (TimeoutError, httpx.HTTPError, httpx.InvalidURL) as failure:
        log.warning("setpoint server call failed: %s", type(failure).__name__)
        return "timeout"
    result = _RESULTS.get(response.status_code)
    if result is None:
        log.warning("setpoint server answered %d, which API-20 does not know", response.status_code)
        return "timeout"
    return result
