import { createHash } from "node:crypto";

import { reportingFeedEnvelopeSha256 } from "./occurrence-export-contract.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EXPECTED_GRAPH_COUNT = 143;
const EXPECTED_CURRENT_MEDIA_COUNT = 2;
const EXPECTED_LEGACY_BY_DASHBOARD = Object.freeze([
    Object.freeze({ uid: "greenhouse-equipment", count: 5 }),
    Object.freeze({ uid: "greenhouse-hydroponics", count: 5 }),
    Object.freeze({ uid: "greenhouse-lighting", count: 13 }),
    Object.freeze({ uid: "greenhouse-soil", count: 10 }),
    Object.freeze({ uid: "greenhouse-weather", count: 7 }),
]);
const EXPECTED_LEGACY_OVERRIDE_COUNT = 40;
const EXPECTED_REPORTING_DEFAULT_COUNT = 103;

const RESULT_KEYS = [
    "contract",
    "schemaVersion",
    "policySha256",
    "sourceOccurrenceManifestSha256",
    "reportingFeedSha256",
    "selectorPreconditionsSha256",
    "datasourceBindingProof",
    "executionBounds",
    "cameraAttempts",
    "graphResult",
    "graphResultSha256",
    "exportBatch",
    "exportBatchSha256",
];

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
    return createHash("sha256").update(value).digest("hex");
}

function exactKeys(value, keys) {
    return (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.getPrototypeOf(value) === Object.prototype &&
        Object.keys(value).join(",") === keys.join(",")
    );
}

function boundedInteger(value, minimum, maximum) {
    return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

function matchesOptional(value, expected) {
    return expected === undefined || value === expected;
}

/**
 * Validate the complete, transport-free producer proof returned by the
 * canonical 143+2 runner. This module is intentionally pure: importing it
 * constructs no renderer, camera transport, client, command, or environment
 * binding.
 */
export function validateOccurrenceProducerResult(
    result,
    {
        policySha256,
        sourceOccurrenceManifestSha256,
        sourceId,
        sourceWatermark,
        sourceWatermarkAt,
    } = {},
) {
    const batch = result?.exportBatch;
    const proof = result?.datasourceBindingProof;
    const bounds = result?.executionBounds;
    const selectorPreconditions = {
        contract: "verdify.lab-occurrence-export-selector-preconditions",
        schemaVersion: 1,
        aggregateExpectedSelectionSha256: batch?.expectedSelectionSha256,
        currentMedia: (batch?.currentMedia ?? []).map(
            ({ occurrenceId, expectedSelectionSha256 }) => ({
                occurrenceId,
                expectedSelectionSha256,
            }),
        ),
    };
    const legacyTotal = Array.isArray(proof?.legacyByDashboard)
        ? proof.legacyByDashboard.reduce(
              (total, record) =>
                  total +
                  (Number.isSafeInteger(record?.count) ? record.count : 0),
              0,
          )
        : -1;
    const exactLegacyProof =
        Array.isArray(proof?.legacyByDashboard) &&
        proof.legacyByDashboard.length > 0 &&
        canonicalBytes(proof.legacyByDashboard).equals(
            canonicalBytes(EXPECTED_LEGACY_BY_DASHBOARD),
        );

    if (
        !exactKeys(result, RESULT_KEYS) ||
        result.contract !== "verdify.lab-occurrence-producer-run" ||
        result.schemaVersion !== 1 ||
        !SHA256_RE.test(result.policySha256) ||
        !SHA256_RE.test(result.sourceOccurrenceManifestSha256) ||
        !matchesOptional(result.policySha256, policySha256) ||
        !matchesOptional(
            result.sourceOccurrenceManifestSha256,
            sourceOccurrenceManifestSha256,
        ) ||
        !SHA256_RE.test(result.reportingFeedSha256) ||
        !SHA256_RE.test(result.selectorPreconditionsSha256) ||
        !SHA256_RE.test(result.graphResultSha256) ||
        !SHA256_RE.test(result.exportBatchSha256) ||
        sha256(canonicalBytes(result.graphResult)) !==
            result.graphResultSha256 ||
        sha256(canonicalBytes(batch)) !== result.exportBatchSha256 ||
        reportingFeedEnvelopeSha256(batch?.reportingFeed) !==
            result.reportingFeedSha256 ||
        sha256(canonicalBytes(selectorPreconditions)) !==
            result.selectorPreconditionsSha256 ||
        batch?.policySha256 !== result.policySha256 ||
        batch?.sourceOccurrenceManifestSha256 !==
            result.sourceOccurrenceManifestSha256 ||
        !matchesOptional(batch?.reportingFeed?.sourceId, sourceId) ||
        !matchesOptional(
            batch?.reportingFeed?.sourceWatermark,
            sourceWatermark,
        ) ||
        !matchesOptional(
            batch?.reportingFeed?.sourceWatermarkAt,
            sourceWatermarkAt,
        ) ||
        batch?.graphs?.length !== EXPECTED_GRAPH_COUNT ||
        batch?.currentMedia?.length !== EXPECTED_CURRENT_MEDIA_COUNT ||
        !exactKeys(proof, [
            "contract",
            "schemaVersion",
            "graphCount",
            "legacyOverrideCount",
            "reportingDefaultCount",
            "legacyByDashboard",
            "planSha256",
        ]) ||
        proof.contract !== "verdify.lab-graph-datasource-binding-proof" ||
        proof.schemaVersion !== 1 ||
        proof.graphCount !== EXPECTED_GRAPH_COUNT ||
        proof.legacyOverrideCount !== EXPECTED_LEGACY_OVERRIDE_COUNT ||
        proof.reportingDefaultCount !== EXPECTED_REPORTING_DEFAULT_COUNT ||
        legacyTotal !== proof.legacyOverrideCount ||
        proof.legacyOverrideCount + proof.reportingDefaultCount !==
            proof.graphCount ||
        !exactLegacyProof ||
        !SHA256_RE.test(proof.planSha256) ||
        !exactKeys(bounds, [
            "graphConcurrency",
            "graphTimeoutMs",
            "graphSettlementGraceMs",
            "graphMaxAttempts",
            "cameraConcurrency",
            "cameraTimeoutMs",
            "cameraMaxAttempts",
        ]) ||
        !boundedInteger(bounds.graphConcurrency, 1, 4) ||
        !boundedInteger(bounds.graphTimeoutMs, 1, 15_000) ||
        !boundedInteger(bounds.graphSettlementGraceMs, 1, 250) ||
        bounds.graphMaxAttempts !== 1 ||
        !boundedInteger(bounds.cameraConcurrency, 1, 2) ||
        !boundedInteger(bounds.cameraTimeoutMs, 1, 15_000) ||
        !boundedInteger(bounds.cameraMaxAttempts, 1, 3) ||
        !Array.isArray(result.cameraAttempts) ||
        result.cameraAttempts.length !== EXPECTED_CURRENT_MEDIA_COUNT ||
        result.cameraAttempts.some(
            (attempt, index) =>
                !exactKeys(attempt, [
                    "occurrenceId",
                    "attempts",
                    "captureStatus",
                ]) ||
                attempt.occurrenceId !==
                    batch.currentMedia[index].occurrenceId ||
                !boundedInteger(
                    attempt.attempts,
                    1,
                    bounds.cameraMaxAttempts,
                ) ||
                attempt.captureStatus !==
                    batch.currentMedia[index].captureStatus,
        )
    ) {
        throw new Error(
            "occurrence producer result does not use the exact canonical runner proof",
        );
    }
    return result;
}

export const occurrenceProducerResultContract = Object.freeze({
    contract: "verdify.lab-occurrence-producer-run",
    schemaVersion: 1,
    expectedGraphCount: EXPECTED_GRAPH_COUNT,
    expectedCurrentMediaCount: EXPECTED_CURRENT_MEDIA_COUNT,
    expectedLegacyOverrideCount: EXPECTED_LEGACY_OVERRIDE_COUNT,
    expectedReportingDefaultCount: EXPECTED_REPORTING_DEFAULT_COUNT,
    expectedLegacyByDashboard: EXPECTED_LEGACY_BY_DASHBOARD,
    bounds: Object.freeze({
        graphConcurrency: Object.freeze([1, 4]),
        graphTimeoutMs: Object.freeze([1, 15_000]),
        graphSettlementGraceMs: Object.freeze([1, 250]),
        graphMaxAttempts: 1,
        cameraConcurrency: Object.freeze([1, 2]),
        cameraTimeoutMs: Object.freeze([1, 15_000]),
        cameraMaxAttempts: Object.freeze([1, 3]),
    }),
});
