import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { expect, test } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const COMPONENT = path.resolve(
    HERE,
    "../../site/quartz/components/GrafanaEmbeds.tsx",
);
const PNG = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    "base64",
);

function productionLoaderScript() {
    const source = readFileSync(COMPONENT, "utf8");
    const marker = "GrafanaEmbeds.afterDOMLoaded = `";
    const start = source.indexOf(marker);
    const suffix = "\n`\n\n  return GrafanaEmbeds";
    const end = source.indexOf(suffix, start + marker.length);

    if (start === -1 || end === -1) {
        throw new Error("Could not extract GrafanaEmbeds.afterDOMLoaded");
    }
    return source.slice(start + marker.length, end);
}

const LOADER = productionLoaderScript();

async function installLoader(page) {
    await page.evaluate((script) => new Function(script)(), LOADER);
}

async function fulfillPng(route) {
    await route.fulfill({
        status: 200,
        contentType: "image/png",
        headers: { "Cache-Control": "no-store" },
        body: PNG,
    });
}

test("@quality Quartz media loader bounds startup work and autoloads intersected dashboards", async ({
    page,
}) => {
    const graphRequests = [];
    const interactiveRequests = [];
    page.on("request", (request) => {
        if (request.url().includes("graph-")) graphRequests.push(request.url());
        if (request.url().startsWith("https://graphs.invalid/")) {
            interactiveRequests.push(request.url());
        }
    });
    await page.route("https://fixture.invalid/**", fulfillPng);
    await page.route("https://graphs.invalid/**", (route) =>
        route.fulfill({
            status: 200,
            contentType: "text/html",
            body: "<!doctype html><title>Interactive Grafana fixture</title>",
        }),
    );

    await page.setContent(`
    <!doctype html>
    <html saved-theme="light">
      <body>
        <div style="height: 5200px"></div>
        <img class="camera-snapshot" loading="lazy"
             data-camera-src="https://fixture.invalid/camera-1.jpg"
             src="https://fixture.invalid/camera-1.jpg" alt="Camera one">
        <img class="camera-snapshot" loading="lazy"
             data-camera-src="https://fixture.invalid/camera-2.jpg"
             src="https://fixture.invalid/camera-2.jpg" alt="Camera two">
        <div style="height: 2800px"></div>
        <div id="near" class="grafana-embed"
             data-iframe-src="https://graphs.invalid/d-solo/near"
             data-image-src="https://fixture.invalid/graph-near.png"
             data-live-src="https://graphs.invalid/d/near"
             data-title="Near panel" data-height="180" data-refresh-ms="0"></div>
        <div style="height: 3200px"></div>
        <div id="far" class="grafana-embed"
             data-iframe-src="https://graphs.invalid/d-solo/far"
             data-image-src="https://fixture.invalid/graph-far.png"
             data-live-src="https://graphs.invalid/d/far"
             data-title="Far panel" data-height="180" data-refresh-ms="0"></div>
      </body>
    </html>
  `);

    await installLoader(page);
    await page.evaluate(() =>
        document.dispatchEvent(
            new CustomEvent("themechange", { detail: { theme: "light" } }),
        ),
    );

    await expect
        .poll(() =>
            page.locator("img.camera-snapshot").evaluateAll((images) =>
                images.map((image) => ({
                    loading: image.loading,
                    legacySource: image.getAttribute("data-camera-src"),
                    autoloadSource: image.getAttribute(
                        "data-camera-autoload-src",
                    ),
                    loaded: image.complete && image.naturalWidth > 0,
                })),
            ),
        )
        .toEqual([
            {
                loading: "eager",
                legacySource: null,
                autoloadSource: "https://fixture.invalid/camera-1.jpg",
                loaded: true,
            },
            {
                loading: "eager",
                legacySource: null,
                autoloadSource: "https://fixture.invalid/camera-2.jpg",
                loaded: true,
            },
        ]);

    // Both graph nodes are outside the observer's preload margin. The initial
    // theme event must not bypass that boundary.
    await page.waitForTimeout(100);
    expect(graphRequests).toEqual([]);
    await expect(page.locator("#near .grafana-embed__placeholder")).toHaveText(
        "Loading...",
    );
    await expect(page.locator("#far .grafana-embed__placeholder")).toHaveText(
        "Loading...",
    );

    await page.locator("#near").scrollIntoViewIfNeeded();
    await expect(page.locator("#near iframe.grafana-embed__frame")).toHaveAttribute(
        "src",
        /\/d-solo\/near\?theme=light$/,
    );
    await expect(page.locator("#near iframe")).toHaveAttribute("loading", "eager");
    await expect(page.locator("#near img")).toHaveCount(0);
    await expect(page.locator("#near button")).toHaveCount(0);
    expect(interactiveRequests.some((url) => url.includes("/d-solo/near"))).toBe(
        true,
    );
    expect(interactiveRequests.some((url) => url.includes("/d-solo/far"))).toBe(
        false,
    );
    expect(graphRequests).toEqual([]);
    await expect(page.locator("#far .grafana-embed__placeholder")).toHaveText(
        "Loading...",
    );

    await page.locator("#far").scrollIntoViewIfNeeded();
    await expect(page.locator("#far iframe.grafana-embed__frame")).toHaveAttribute(
        "src",
        /\/d-solo\/far\?theme=light$/,
    );
    expect(interactiveRequests.some((url) => url.includes("/d-solo/far"))).toBe(
        true,
    );
    await expect(page.locator("#near iframe")).toHaveCount(0);
    await expect(page.locator("#near .grafana-embed__placeholder")).toHaveText(
        "Loading...",
    );
});

test("@quality Quartz dashboard automatically falls back when its iframe stalls", async ({
    page,
}) => {
    await page.route("https://fixture.invalid/**", fulfillPng);
    await page.route("https://graphs.invalid/**", (route) =>
        route.fulfill({
            status: 200,
            contentType: "text/html",
            body: "<!doctype html><title>Interactive Grafana fixture</title>",
        }),
    );
    await page.setContent(`
    <!doctype html>
    <html saved-theme="light">
      <body>
        <div id="fallback" class="grafana-embed"
             data-iframe-src="https://graphs.invalid/d-solo/fallback"
             data-image-src="https://fixture.invalid/graph-fallback.png"
             data-live-src="https://graphs.invalid/d/fallback"
             data-title="Fallback panel" data-height="180" data-refresh-ms="0"></div>
      </body>
    </html>
  `);

    // Suppress the fixture iframe's load/error listeners and compress only the
    // production 12-second watchdog, deterministically simulating a navigation
    // that never settles without weakening any other loader behavior.
    await page.evaluate(() => {
        const nativeAddEventListener =
            HTMLIFrameElement.prototype.addEventListener;
        HTMLIFrameElement.prototype.addEventListener = function (
            type,
            listener,
            options,
        ) {
            if (type === "load" || type === "error") return;
            return nativeAddEventListener.call(this, type, listener, options);
        };
        const nativeTimeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay = 0, ...args) =>
            nativeTimeout(callback, delay === 12000 ? 5 : delay, ...args);
    });

    await installLoader(page);
    await expect(
        page.locator("#fallback img.grafana-embed__img"),
    ).toHaveJSProperty("naturalWidth", 1);
    await expect(page.locator("#fallback iframe")).toHaveCount(0);
    await expect(page.locator("#fallback button")).toHaveText(
        "Retry interactive panel",
    );
});

test("@quality Quartz recycles iframe contexts across a graph-heavy page", async ({
    page,
}) => {
    const interactiveRequests = [];
    page.on("request", (request) => {
        if (request.url().startsWith("https://graphs.invalid/")) {
            interactiveRequests.push(request.url());
        }
    });
    await page.route("https://graphs.invalid/**", (route) =>
        route.fulfill({
            status: 200,
            contentType: "text/html",
            body: "<!doctype html><title>Interactive Grafana fixture</title>",
        }),
    );
    const embeds = Array.from(
        { length: 17 },
        (_, index) => `
        <div style="height: 500px"></div>
        <div id="graph-heavy-${index}" class="grafana-embed"
             data-iframe-src="https://graphs.invalid/d-solo/heavy-${index}"
             data-title="Heavy panel ${index}" data-height="180" data-refresh-ms="0"></div>`,
    ).join("");
    await page.setContent(
        `<!doctype html><html saved-theme="light"><body>${embeds}</body></html>`,
    );
    await installLoader(page);

    let maxActive = 0;
    for (let index = 0; index < 17; index += 1) {
        await page.locator(`#graph-heavy-${index}`).scrollIntoViewIfNeeded();
        await expect(
            page.locator(`#graph-heavy-${index} iframe.grafana-embed__frame`),
        ).toHaveCount(1);
        maxActive = Math.max(
            maxActive,
            await page.locator("iframe.grafana-embed__frame").count(),
        );
    }

    expect(maxActive).toBeLessThan(17);
    await expect(page.locator("#graph-heavy-0 iframe")).toHaveCount(0);
    await page.locator("#graph-heavy-0").scrollIntoViewIfNeeded();
    await expect(
        page.locator("#graph-heavy-0 iframe.grafana-embed__frame"),
    ).toHaveCount(1);
    expect(
        interactiveRequests.filter((url) => url.includes("/d-solo/heavy-0")),
    ).toHaveLength(2);
});

test("@quality Quartz graph image recovers after its initial retry budget", async ({
    page,
}) => {
    let requests = 0;
    const requestUrls = [];

    await page.route(
        "https://fixture.invalid/graph-recover.png**",
        async (route) => {
            requests += 1;
            requestUrls.push(route.request().url());
            if (requests <= 4) {
                await route.fulfill({
                    status: 500,
                    contentType: "text/plain",
                    headers: { "Cache-Control": "no-store" },
                    body: "render pending",
                });
                return;
            }
            await fulfillPng(route);
        },
    );

    await page.setContent(`
    <!doctype html>
    <html saved-theme="light">
      <body>
        <div id="recover" class="grafana-embed"
             data-iframe-src="https://graphs.invalid/d-solo/recover"
             data-image-src="https://fixture.invalid/graph-recover.png"
             data-live-src="https://graphs.invalid/d/recover"
             data-title="Recovering panel" data-height="180"></div>
      </body>
    </html>
  `);

    // Keep the production retry/refresh sequence but compress long wall-clock
    // delays so the test remains deterministic and offline. Capture the normal
    // one-minute refresh callback instead of racing it against the initial retry
    // sequence; the test invokes it after observing the unavailable state.
    await page.evaluate(() => {
        const nativeTimeout = window.setTimeout.bind(window);
        const nativeInterval = window.setInterval.bind(window);
        window.__graphRefreshCallbacks = [];
        window.setTimeout = (callback, delay = 0, ...args) =>
            nativeTimeout(
                callback,
                delay === 3000 || delay === 6000 || delay === 12000 ? 5 : delay,
                ...args,
            );
        window.setInterval = (callback, delay = 0, ...args) => {
            if (delay >= 60000) {
                window.__graphRefreshCallbacks.push(() => callback(...args));
                return 424242;
            }
            return nativeInterval(callback, delay, ...args);
        };
    });

    await installLoader(page);
    await expect(
        page.locator("#recover .grafana-embed__placeholder"),
    ).toHaveText("Image render unavailable. Tap below to open live panel.");
    expect(requests).toBe(4);
    expect(
        new URL(requestUrls[0]).searchParams.has("_qts"),
        "initial render must retain its stable URL",
    ).toBe(false);
    const retryMarkers = requestUrls
        .slice(1)
        .map((url) => new URL(url).searchParams.get("_qts"));
    expect(retryMarkers.every(Boolean)).toBe(true);
    expect(new Set(retryMarkers).size).toBe(3);
    expect(
        requestUrls.slice(1).every((url) => {
            const refreshUrl = new URL(url);
            const firstKey = refreshUrl.searchParams.keys().next().value;
            refreshUrl.searchParams.delete("_qts");
            return firstKey === "_qts" && refreshUrl.href === requestUrls[0];
        }),
        "browser markers must normalize to the initial shared-cache URL",
    ).toBe(true);
    await expect(page.locator("#recover img.grafana-embed__img")).toHaveCount(
        0,
    );

    await page.evaluate(() => {
        if (window.__graphRefreshCallbacks.length !== 1) {
            throw new Error(
                `Expected one graph refresh callback, got ${window.__graphRefreshCallbacks.length}`,
            );
        }
        window.__graphRefreshCallbacks[0]();
    });
    await expect(
        page.locator("#recover img.grafana-embed__img"),
    ).toHaveJSProperty("naturalWidth", 1);
    await expect(
        page.locator("#recover .grafana-embed__placeholder"),
    ).toHaveCount(0);
    expect(requests).toBeGreaterThanOrEqual(5);
    const refreshMarker = new URL(requestUrls[4]).searchParams.get("_qts");
    expect(refreshMarker).toBeTruthy();
    expect(new Set([...retryMarkers, refreshMarker]).size).toBe(4);
});

test("@quality Quartz theme switch cancels the obsolete graph render generation", async ({
    page,
}) => {
    const requestUrls = [];
    await page.route(
        "https://fixture.invalid/theme-graph.png**",
        async (route) => {
            const url = route.request().url();
            requestUrls.push(url);
            if (new URL(url).searchParams.get("theme") === "light") {
                await route.fulfill({
                    status: 500,
                    body: "light render failed",
                });
                return;
            }
            await fulfillPng(route);
        },
    );
    await page.setContent(`
    <!doctype html>
    <html saved-theme="light">
      <body>
        <div id="themed" class="grafana-embed"
             data-image-src="https://fixture.invalid/theme-graph.png?panelId=1"
             data-title="Theme panel" data-height="180" data-refresh-ms="0"></div>
      </body>
    </html>
  `);
    await installLoader(page);
    await expect.poll(() => requestUrls.length).toBe(1);
    // Let the image error handler register its retry before the replacement
    // generation clears it. The native three-second backoff makes the ordering
    // deterministic instead of racing Playwright's polling interval.
    await page.waitForTimeout(25);
    await page.evaluate(() => {
        document.documentElement.setAttribute("saved-theme", "dark");
        document.dispatchEvent(
            new CustomEvent("themechange", { detail: { theme: "dark" } }),
        );
    });
    await expect(
        page.locator("#themed img.grafana-embed__img"),
    ).toHaveJSProperty("naturalWidth", 1);
    await page.waitForTimeout(3200);

    expect(requestUrls).toHaveLength(2);
    expect(new URL(requestUrls[0]).searchParams.get("theme")).toBe("light");
    expect(new URL(requestUrls[1]).searchParams.get("theme")).toBe("dark");
});

test("@quality Quartz graph queue holds at two browser requests", async ({
    page,
}) => {
    const held = [];
    await page.route(
        "https://fixture.invalid/held-graph-*.png**",
        (route) =>
            new Promise((resolve) => {
                held.push({
                    release: async () => {
                        await fulfillPng(route);
                        resolve();
                    },
                });
            }),
    );
    const embeds = Array.from(
        { length: 5 },
        (_, index) => `
        <div id="held-${index}" class="grafana-embed"
             data-image-src="https://fixture.invalid/held-graph-${index}.png"
             data-title="Held panel ${index}" data-height="100" data-refresh-ms="0"></div>`,
    ).join("");
    await page.setContent(
        `<!doctype html><html saved-theme="light"><body>${embeds}</body></html>`,
    );

    await installLoader(page);
    await expect.poll(() => held.length).toBe(2);
    await page.waitForTimeout(100);
    expect(held).toHaveLength(2);

    await held[0].release();
    await expect.poll(() => held.length).toBe(3);
    await Promise.all([held[1].release(), held[2].release()]);
    await expect.poll(() => held.length).toBe(5);
    await Promise.all([held[3].release(), held[4].release()]);

    await expect(page.locator("img.grafana-embed__img")).toHaveCount(5);
    await expect
        .poll(() =>
            page
                .locator("img.grafana-embed__img")
                .evaluateAll((images) =>
                    images.every((image) => image.naturalWidth === 1),
                ),
        )
        .toBe(true);
});
