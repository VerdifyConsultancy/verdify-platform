export const graphExportProducerContract = Object.freeze({
  expectedGraphCount: 143,
  defaultConcurrency: 4,
  maxConcurrency: 4,
  defaultTimeoutMs: 10_000,
  maxTimeoutMs: 15_000,
  defaultSettlementGraceMs: 50,
  maxSettlementGraceMs: 250,
  renderer: Object.freeze({
    contract: "verdify.lab-graph-renderer",
    schemaVersion: 3,
    sourceClass: "operator-owned-reporting-tier",
    anonymousAccess: false,
    reportingFeedSha256: "required-exact-plan-digest",
    reportingDatasourceIdentitySha256: "required-dedicated-identity-digest",
    abortCooperation: "settle-within-grace-after-abort",
  }),
  legacyDatasourceDashboardUids: Object.freeze([
    "greenhouse-equipment",
    "greenhouse-hydroponics",
    "greenhouse-lighting",
    "greenhouse-soil",
    "greenhouse-weather",
  ]),
  probeStatuses: Object.freeze(["success", "timeout", "http-error", "decode-error", "missing"]),
});

export const cameraExportProducerContract = Object.freeze({
  approvedOccurrenceIds: Object.freeze([
    "media_024bdac9f86794c7d1f36d48",
    "media_4e973f995789201d00aed8fd",
  ]),
  defaultTimeoutMs: 10_000,
  maxTimeoutMs: 15_000,
});

export const occurrenceProducerRunnerContract = Object.freeze({
  expectedGraphCount: 143,
  expectedCurrentMediaCount: 2,
  expectedLegacyOverrideCount: 40,
  expectedReportingDefaultCount: 103,
  defaultCameraConcurrency: 2,
  maxCameraConcurrency: 2,
  defaultCameraMaxAttempts: 2,
  maxCameraMaxAttempts: 3,
  retryableCameraStatuses: Object.freeze([
    "timeout",
    "http-error",
    "decode-error",
    "missing",
  ]),
  selectorReader: Object.freeze({
    contract: "verdify.lab-occurrence-selector-precondition-reader",
    schemaVersion: 1,
  }),
  result: Object.freeze({
    contract: "verdify.lab-occurrence-producer-run",
    schemaVersion: 1,
  }),
});
