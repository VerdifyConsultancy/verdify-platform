import {
    LocalSiteReleaseStore,
    S3SiteReleaseStore,
    createSiteReleaseStore,
    parseSiteReleaseStoreLocation,
    publishSiteRelease,
} from "./site-release-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;

/**
 * Bind the occurrence-to-site processor to one already-initialized writer.
 * Store construction and authority selection stay outside this adapter.
 */
export function createSiteReleasePublicationOperation({ storeRoot, store }) {
    const location = parseSiteReleaseStoreLocation(storeRoot);
    const expectedIdentity = createSiteReleaseStore(storeRoot).identity.sha256;
    const StoreClass =
        location.kind === "local" ? LocalSiteReleaseStore : S3SiteReleaseStore;
    if (
        !(store instanceof StoreClass) ||
        store.identity?.sha256 !== expectedIdentity ||
        (location.kind === "s3" && store.accessMode !== "writer") ||
        !SHA256_RE.test(store.identity?.sha256 ?? "") ||
        [
            "readSelection",
            "readRelease",
            "readBlob",
            "readEventIntent",
        ].some((method) => typeof store[method] !== "function")
    ) {
        throw new Error("site release publication writer is invalid");
    }
    return {
        contract: "verdify.lab-site-release-publication-operation",
        schemaVersion: 1,
        storeIdentitySha256: store.identity.sha256,
        readSelection: () => store.readSelection(),
        readRelease: (releaseSha256) => store.readRelease(releaseSha256),
        readBlob: (blobSha256, options) => store.readBlob(blobSha256, options),
        readEventIntent: (eventId) => store.readEventIntent(eventId),
        publish: (request) =>
            publishSiteRelease({ ...request, storeRoot, store }),
    };
}
