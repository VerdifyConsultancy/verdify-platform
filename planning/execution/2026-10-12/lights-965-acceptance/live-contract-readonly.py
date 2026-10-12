"""Retry-only standard-library readback, executed with python -I -S.

The external owner binds actual Pod/container/image identities before/after.
No application import, environment read, credential or command request occurs.
"""

import ast
import hashlib
import json
import urllib.request
from pathlib import Path

raw = Path("/app/main.py").read_bytes()
source = raw.decode()
tree = ast.parse(source)
function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "control_lights")
segment = ast.get_source_segment(source, function).encode()
request = urllib.request.Request("http://127.0.0.1:8080/openapi.json", method="GET")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


# Explicitly disable proxy-environment lookup and redirects outside loopback.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
with opener.open(request, timeout=5) as response:
    status = response.status
    contract_raw = response.read(2_000_001)
assert status == 200 and len(contract_raw) <= 2_000_000
contract = json.loads(contract_raw)
route = "/api/v1/greenhouses/{greenhouse_id}/lights/{circuit}/{action}"
operation = contract["paths"][route]["post"]
print(
    json.dumps(
        {
            "schema": "verdify-965-readonly-live-contract-v1",
            "api_source_file_sha256": hashlib.sha256(raw).hexdigest(),
            "control_lights_function_sha256": hashlib.sha256(segment).hexdigest(),
            "openapi_http_status": status,
            "openapi_bytes": len(contract_raw),
            "openapi_sha256": hashlib.sha256(contract_raw).hexdigest(),
            "route": route,
            "method": "post",
            "response_codes": sorted(operation["responses"]),
            "declared_501_without_200": "501" in operation["responses"] and "200" not in operation["responses"],
            "live_POST_executed": False,
            "physical_command_or_readback_qualified": False,
        },
        sort_keys=True,
    )
)
