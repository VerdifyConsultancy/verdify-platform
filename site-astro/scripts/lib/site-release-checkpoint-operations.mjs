import { createHash } from "node:crypto";

import { S3SiteReleaseStore } from "./site-release-store.mjs";

const EVENT_ID_RE = /^evt_occurrence_site_[0-9a-f]{32}$/u;
const MAX_CHECKPOINT_BYTES = 64 * 1024;

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

function checkpointKey(eventId) {
    if (!EVENT_ID_RE.test(eventId)) {
        throw new Error("occurrence site checkpoint event ID is invalid");
    }
    return `checkpoints/sha256/${sha256(Buffer.from(eventId))}.json`;
}

function parseCheckpoint(value, eventId) {
    let document;
    try {
        document = JSON.parse(value.bytes.toString("utf8"));
    } catch {
        throw new Error("occurrence site checkpoint is not valid JSON");
    }
    if (
        document === null ||
        typeof document !== "object" ||
        Array.isArray(document) ||
        document.eventId !== eventId ||
        !canonicalBytes(document).equals(value.bytes)
    ) {
        throw new Error("occurrence site checkpoint is not canonical");
    }
    return {
        document,
        bytes: value.bytes,
        sha256: sha256(value.bytes),
    };
}

/** Bind immutable, per-event publisher checkpoints to the selected S3 site store. */
export function createSiteReleaseCheckpointOperations({ store } = {}) {
    if (
        !(store instanceof S3SiteReleaseStore) ||
        store.accessMode !== "writer" ||
        typeof store.objects?.read !== "function" ||
        typeof store.objects?.putIfAbsent !== "function"
    ) {
        throw new Error("occurrence site checkpoint writer is invalid");
    }
    return {
        contract: "verdify.lab-occurrence-site-checkpoint-operations",
        schemaVersion: 1,
        storeIdentitySha256: store.identity.sha256,
        async read(eventId) {
            const value = await store.objects.read(checkpointKey(eventId), {
                maximumBytes: MAX_CHECKPOINT_BYTES,
                label: "occurrence site checkpoint",
                missing: true,
            });
            return value === null ? null : parseCheckpoint(value, eventId);
        },
        async write(document) {
            if (!EVENT_ID_RE.test(document?.eventId ?? "")) {
                throw new Error("occurrence site checkpoint is invalid");
            }
            const bytes = canonicalBytes(document);
            if (bytes.length < 1 || bytes.length > MAX_CHECKPOINT_BYTES) {
                throw new Error("occurrence site checkpoint exceeds its byte limit");
            }
            const key = checkpointKey(document.eventId);
            const result = await store.objects.putIfAbsent(key, bytes, {
                contentType: "application/json",
            });
            if (!result.written) {
                const existing = await store.objects.read(key, {
                    maximumBytes: MAX_CHECKPOINT_BYTES,
                    label: "occurrence site checkpoint",
                });
                if (!existing.bytes.equals(bytes)) {
                    throw new Error(
                        "occurrence site checkpoint event ID was reused",
                    );
                }
            }
            return parseCheckpoint({ bytes }, document.eventId);
        },
    };
}

export const siteReleaseCheckpointContract = Object.freeze({
    contract: "verdify.lab-occurrence-site-checkpoint-operations",
    schemaVersion: 1,
    storage: "immutable-s3-event-record",
    maximumBytes: MAX_CHECKPOINT_BYTES,
});
