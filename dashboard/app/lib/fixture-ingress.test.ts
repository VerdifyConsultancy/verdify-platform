// SPDX-License-Identifier: MIT
// The browser suite's ingress (#1128), in process between two stub upstreams on free ports: which requests reach the
// fixture API and which the web server, the Host and the session cookie each gets, and the guard that keeps it in CI.
import { createServer, request, type IncomingHttpHeaders, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, expect, test } from "vitest";
import { FIXTURE_SESSION, createIngress, startIngress } from "../../e2e/fixture-ingress.mjs";

const COOKIE = `__Host-verdify_session=${FIXTURE_SESSION}`;
type Seen = { url: string | undefined; method: string | undefined; headers: IncomingHttpHeaders; body: string };
const servers: Server[] = [];

function serve(server: Server): Promise<number> {
  servers.push(server);
  return new Promise((resolve) => { server.listen(0, "127.0.0.1", () => resolve((server.address() as AddressInfo).port)); });
}
/** A stub upstream that answers with what it was sent, and its name. */
function stub(name: string) {
  return createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    res.writeHead(200, { "Content-Type": "application/json", "X-Upstream": name, Connection: "keep-alive" });
    res.end(JSON.stringify({ url: req.url, method: req.method, headers: req.headers, body } satisfies Seen));
  });
}

let ingressPort = 0;
beforeAll(async () => {
  const [apiPort, webPort] = await Promise.all([serve(stub("api")), serve(stub("web"))]);
  ingressPort = await serve(createIngress({ apiPort, webPort }));
});
afterAll(() => { for (const server of servers) { server.close(); server.closeAllConnections(); } });

function send(path: string, { method = "GET", headers = {}, body }: { method?: string; headers?: Record<string, string>; body?: string } = {}) {
  return new Promise<{ upstream: unknown; connection: unknown; seen: Seen }>((resolve, reject) => {
    const req = request({ hostname: "127.0.0.1", port: ingressPort, path, method, headers: { host: "localhost:4173", ...headers } }, async (res) => {
      let text = "";
      for await (const chunk of res) text += chunk;
      resolve({ upstream: res.headers["x-upstream"], connection: res.headers.connection, seen: JSON.parse(text) as Seen });
    });
    req.on("error", reject);
    req.end(body);
  });
}

test("/api/v1/* reaches the fixture API as /v1/*, query and body intact, with no session cookie added", async () => {
  const { upstream, seen } = await send("/api/v1/settings/mister_engage_kpa?x=1", { method: "PUT", headers: { "Idempotency-Key": "k" }, body: "{\"value\":1.5}" });
  expect(upstream).toBe("api");
  expect(seen).toMatchObject({ url: "/v1/settings/mister_engage_kpa?x=1", method: "PUT", body: "{\"value\":1.5}" });
  expect(seen.headers["idempotency-key"]).toBe("k");
  expect(seen.headers.cookie).toBeUndefined();
});

test("a page reaches the web server as dashboard.verdify.ai with the fixture session, beside any cookie it already has", async () => {
  const plain = await send("/water/history?range=7d");
  expect(plain.upstream).toBe("web");
  expect(plain.seen).toMatchObject({ url: "/water/history?range=7d" });
  expect(plain.seen.headers.host).toBe("dashboard.verdify.ai");
  expect(plain.seen.headers.cookie).toBe(COOKIE);
  expect((await send("/", { headers: { cookie: "other=1" } })).seen.headers.cookie).toBe(`other=1; ${COOKIE}`);
  // Only /api/v1/ goes to the API: the BFF answers anything else under /api, and /apiary is a page.
  expect((await send("/api/other")).upstream).toBe("web");
  expect((await send("/apiary")).seen.headers.cookie).toBe(COOKIE);
});

test("under /auth/ no cookie is added, so the sign-in cards render as for a signed-out browser", async () => {
  for (const path of ["/auth/login", "/auth/callback?code=x&state=y"]) {
    const { upstream, seen } = await send(path);
    expect(upstream, path).toBe("web");
    expect(seen.headers.host, path).toBe("dashboard.verdify.ai");
    expect(seen.headers.cookie, path).toBeUndefined();
  }
  expect((await send("/authors")).seen.headers.cookie).toBe(COOKIE);
});

test("hop-by-hop headers stay on their own side of the ingress", async () => {
  const { connection, seen } = await send("/", { headers: { connection: "keep-alive", "keep-alive": "timeout=5" } });
  expect(seen.headers["keep-alive"]).toBeUndefined();
  expect(seen.headers.connection).toBe("close");
  expect(connection).not.toBe("close");
});

test("it refuses to start anywhere but Linux with DASHBOARD_CI_IN_CLUSTER=1", () => {
  expect(() => startIngress({}, "linux")).toThrow("Fixture ingress requires approved in-cluster CI");
  expect(() => startIngress({ DASHBOARD_CI_IN_CLUSTER: "1" }, "darwin")).toThrow("Fixture ingress requires approved in-cluster CI");
});
