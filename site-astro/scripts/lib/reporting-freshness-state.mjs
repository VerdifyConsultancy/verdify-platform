const SHA256_RE = /^[0-9a-f]{64}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;

const WINDOW_SAMPLE_COUNT = 96;
const WINDOW_SECONDS = 24 * 60 * 60;
const SAMPLE_CADENCE_SECONDS = 15 * 60;
const COMPLETE_WINDOW_COVERAGE_SECONDS =
    (WINDOW_SAMPLE_COUNT - 1) * SAMPLE_CADENCE_SECONDS;
const WINDOW_AGE_BOUNDARY = "(evaluatedAt-86400s,evaluatedAt]";
const SAMPLE_ANCHOR = "utc-quarter-hour";
const MAX_CONSECUTIVE_EVALUATION_GAP_SECONDS = 5 * 60;
const TARGET_LAG_SECONDS = 15 * 60;
const ALERT_LAG_SECONDS = 30 * 60;
const REQUIRED_CONSECUTIVE_EVALUATIONS = 2;
const MAX_LAG_SECONDS = 365 * 24 * 60 * 60;
const MAX_STATE_BYTES = 64 * 1024;
const METRIC_NAME = "verdify_lab_reporting_source_lag_seconds";

function exactKeys(value, keys) {
    return (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.getPrototypeOf(value) === Object.prototype &&
        Object.keys(value).join(",") === keys.join(",")
    );
}

function canonicalInstant(value, label) {
    if (typeof value !== "string" || !ISO_INSTANT_RE.test(value)) {
        throw new Error(`${label} is invalid`);
    }
    const milliseconds = Date.parse(value);
    const normalized = Number.isFinite(milliseconds)
        ? new Date(milliseconds).toISOString()
        : "";
    const expected = value.includes(".")
        ? normalized
        : normalized.replace(".000Z", "Z");
    if (value !== expected) throw new Error(`${label} is invalid`);
    return value;
}

function lagSeconds(value, { nullable = false } = {}) {
    if (nullable && value === null) return null;
    if (!Number.isSafeInteger(value) || value < 0 || value > MAX_LAG_SECONDS) {
        throw new Error("reporting freshness lag is invalid");
    }
    return value;
}

function outputSha256(value, { nullable = false } = {}) {
    if (nullable && value === null) return null;
    if (typeof value !== "string" || !SHA256_RE.test(value)) {
        throw new Error("reporting freshness output digest is invalid");
    }
    return value;
}

function validateEvaluation(value, label = "reporting freshness evaluation") {
    if (!exactKeys(value, ["evaluatedAt", "lagSeconds", "outputSha256"])) {
        throw new Error(`${label} does not use the closed v1 shape`);
    }
    canonicalInstant(value.evaluatedAt, `${label} time`);
    lagSeconds(value.lagSeconds, { nullable: true });
    outputSha256(value.outputSha256, { nullable: true });
    if (value.lagSeconds === null && value.outputSha256 !== null) {
        throw new Error(`${label} cannot bind output to a missing lag sample`);
    }
    return value;
}

function isQuarterHourAnchor(value) {
    return Date.parse(value) % (SAMPLE_CADENCE_SECONDS * 1000) === 0;
}

function validateLastKnownGood(value) {
    if (value === null) return value;
    if (!exactKeys(value, ["outputSha256", "verifiedAt", "lagSeconds"])) {
        throw new Error(
            "reporting freshness LKG does not use the closed v1 shape",
        );
    }
    outputSha256(value.outputSha256);
    canonicalInstant(
        value.verifiedAt,
        "reporting freshness LKG verification time",
    );
    lagSeconds(value.lagSeconds);
    if (value.lagSeconds > TARGET_LAG_SECONDS) {
        throw new Error("reporting freshness LKG is not target-fresh");
    }
    return value;
}

function boundedStreak(value, label) {
    if (
        !Number.isSafeInteger(value) ||
        value < 0 ||
        value > REQUIRED_CONSECUTIVE_EVALUATIONS
    )
        throw new Error(`${label} is invalid`);
    return value;
}

function validatePersistedHysteresis(value) {
    const { lastEvaluation } = value;
    if (lastEvaluation === null) {
        if (
            value.alertState !== "inactive" ||
            value.consecutiveAboveAlert !== 0 ||
            value.consecutiveBelowRecovery !== 0
        ) {
            throw new Error(
                "reporting freshness hysteresis is unreachable without an evaluation",
            );
        }
        return;
    }

    const lag = lastEvaluation.lagSeconds;
    let expectedAbove = 0;
    let expectedBelow = 0;
    if (lag !== null && lag > ALERT_LAG_SECONDS) {
        expectedAbove =
            value.alertState === "firing"
                ? REQUIRED_CONSECUTIVE_EVALUATIONS
                : 1;
    } else if (
        lag !== null &&
        lag < TARGET_LAG_SECONDS &&
        value.alertState === "firing"
    ) {
        expectedBelow = 1;
    }
    if (
        value.consecutiveAboveAlert !== expectedAbove ||
        value.consecutiveBelowRecovery !== expectedBelow
    ) {
        throw new Error(
            "reporting freshness hysteresis is inconsistent with its last evaluation",
        );
    }

    if (
        value.alertState === "firing" &&
        value.revision <
            (lag !== null && lag > ALERT_LAG_SECONDS
                ? REQUIRED_CONSECUTIVE_EVALUATIONS
                : REQUIRED_CONSECUTIVE_EVALUATIONS + 1)
    ) {
        throw new Error("reporting freshness firing state is unreachable");
    }
}

function validateState(value) {
    if (
        !exactKeys(value, [
            "contract",
            "schemaVersion",
            "revision",
            "samples",
            "lastEvaluation",
            "alertState",
            "consecutiveAboveAlert",
            "consecutiveBelowRecovery",
            "lastKnownGood",
        ]) ||
        value.contract !== "verdify.lab-reporting-freshness-state" ||
        value.schemaVersion !== 1 ||
        !Number.isSafeInteger(value.revision) ||
        value.revision < 0 ||
        !Array.isArray(value.samples) ||
        value.samples.length > WINDOW_SAMPLE_COUNT ||
        value.revision < value.samples.length ||
        !["inactive", "firing"].includes(value.alertState)
    )
        throw new Error(
            "reporting freshness state does not use the closed v1 shape",
        );

    boundedStreak(
        value.consecutiveAboveAlert,
        "reporting freshness alert streak",
    );
    boundedStreak(
        value.consecutiveBelowRecovery,
        "reporting freshness recovery streak",
    );
    if (value.consecutiveAboveAlert > 0 && value.consecutiveBelowRecovery > 0)
        throw new Error("reporting freshness state has conflicting streaks");

    let previousTime = -1;
    for (const sample of value.samples) {
        validateEvaluation(sample, "reporting freshness sample");
        if (
            sample.lagSeconds === null ||
            !isQuarterHourAnchor(sample.evaluatedAt)
        ) {
            throw new Error(
                "reporting freshness window contains a missing or unanchored sample",
            );
        }
        const sampleTime = Date.parse(sample.evaluatedAt);
        if (sampleTime <= previousTime) {
            throw new Error(
                "reporting freshness samples are not strictly ordered",
            );
        }
        previousTime = sampleTime;
    }

    if (value.lastEvaluation !== null) {
        validateEvaluation(
            value.lastEvaluation,
            "last reporting freshness evaluation",
        );
        const lastEvaluationTime = Date.parse(value.lastEvaluation.evaluatedAt);
        if (
            lastEvaluationTime < previousTime ||
            (value.lastEvaluation.lagSeconds === null &&
                value.samples.length > 0 &&
                lastEvaluationTime === previousTime)
        ) {
            throw new Error(
                "last reporting freshness evaluation predates the sample window",
            );
        }
        const exclusiveLowerBoundary =
            lastEvaluationTime - WINDOW_SECONDS * 1000;
        if (
            value.samples.some(
                (sample) =>
                    Date.parse(sample.evaluatedAt) <= exclusiveLowerBoundary,
            )
        ) {
            throw new Error(
                "reporting freshness sample is outside the rolling 24-hour window",
            );
        }
        const lastEvaluationIsWindowSample =
            value.lastEvaluation.lagSeconds !== null &&
            isQuarterHourAnchor(value.lastEvaluation.evaluatedAt);
        if (
            lastEvaluationIsWindowSample &&
            JSON.stringify(value.lastEvaluation) !==
                JSON.stringify(value.samples.at(-1))
        )
            throw new Error(
                "last reporting freshness evaluation is not the newest sample",
            );
    } else if (value.samples.length > 0 || value.revision !== 0) {
        throw new Error(
            "reporting freshness state is missing its last evaluation",
        );
    }

    validatePersistedHysteresis(value);

    validateLastKnownGood(value.lastKnownGood);
    if (value.lastKnownGood !== null && value.revision < WINDOW_SAMPLE_COUNT) {
        throw new Error(
            "reporting freshness LKG predates a complete sample window",
        );
    }
    if (
        value.lastKnownGood !== null &&
        (value.lastEvaluation === null ||
            Date.parse(value.lastKnownGood.verifiedAt) >
                Date.parse(value.lastEvaluation.evaluatedAt))
    )
        throw new Error("reporting freshness LKG is newer than the state");
    return value;
}

function canonicalStateText(value) {
    return `${JSON.stringify(value, null, 2)}\n`;
}

function percentile95(samples) {
    if (samples.length === 0) return null;
    const ordered = samples
        .map(({ lagSeconds: lag }) => lag)
        .sort((left, right) => left - right);
    return ordered[Math.ceil(ordered.length * 0.95) - 1];
}

function pruneRollingWindow(samples, evaluatedAt) {
    const upperBoundary = Date.parse(evaluatedAt);
    const exclusiveLowerBoundary = upperBoundary - WINDOW_SECONDS * 1000;
    return samples
        .filter((sample) => {
            const sampleTime = Date.parse(sample.evaluatedAt);
            return (
                sampleTime > exclusiveLowerBoundary &&
                sampleTime <= upperBoundary
            );
        })
        .slice(-WINDOW_SAMPLE_COUNT);
}

function windowCoverage(samples) {
    const sampleTimes = samples.map(({ evaluatedAt }) =>
        Date.parse(evaluatedAt),
    );
    const coverageSeconds =
        sampleTimes.length < 2
            ? 0
            : Math.floor((sampleTimes.at(-1) - sampleTimes[0]) / 1000);
    const cadenceValid = sampleTimes
        .slice(1)
        .every(
            (sampleTime, index) =>
                sampleTime - sampleTimes[index] ===
                SAMPLE_CADENCE_SECONDS * 1000,
        );
    return {
        coverageSeconds,
        cadenceValid,
        complete:
            samples.length === WINDOW_SAMPLE_COUNT &&
            cadenceValid &&
            coverageSeconds === COMPLETE_WINDOW_COVERAGE_SECONDS,
    };
}

function windowStatus(coverage, p95LagSeconds) {
    if (!coverage.complete) return "insufficient";
    if (p95LagSeconds > ALERT_LAG_SECONDS) return "stale";
    if (p95LagSeconds > TARGET_LAG_SECONDS) return "late";
    return "target";
}

function windowModel(samples) {
    const coverage = windowCoverage(samples);
    const p95LagSeconds = percentile95(samples);
    return {
        sampleCount: samples.length,
        minimumSampleCount: WINDOW_SAMPLE_COUNT,
        maximumSampleCount: WINDOW_SAMPLE_COUNT,
        windowSeconds: WINDOW_SECONDS,
        sampleCadenceSeconds: SAMPLE_CADENCE_SECONDS,
        sampleAnchor: SAMPLE_ANCHOR,
        ageBoundary: WINDOW_AGE_BOUNDARY,
        coverageSeconds: coverage.coverageSeconds,
        cadenceValid: coverage.cadenceValid,
        p95LagSeconds,
        targetLagSeconds: TARGET_LAG_SECONDS,
        status: windowStatus(coverage, p95LagSeconds),
    };
}

function metricModel(evaluation, disposition) {
    return {
        contract: "verdify.lab-reporting-source-lag-metric",
        schemaVersion: 1,
        name: METRIC_NAME,
        type: "gauge",
        unit: "seconds",
        help: "Lag between the Lab reporting source watermark and its evaluation.",
        maximumSeries: 1,
        labels: {},
        sample:
            disposition === "accepted" && evaluation.lagSeconds !== null
                ? {
                      observedAt: evaluation.evaluatedAt,
                      value: evaluation.lagSeconds,
                  }
                : null,
    };
}

function ignoredResult(state, evaluation, disposition) {
    const selectedOutputSha256 = state.lastKnownGood?.outputSha256 ?? null;
    return {
        contract: "verdify.lab-reporting-freshness-evaluation",
        schemaVersion: 1,
        evaluatedAt: evaluation.evaluatedAt,
        sampleDisposition: disposition,
        windowSampleDisposition: "ignored",
        window: windowModel(state.samples),
        alert: {
            state: state.alertState,
            transition: "none",
            evaluation: "ignored",
            consecutiveAboveAlert: state.consecutiveAboveAlert,
            consecutiveBelowRecovery: state.consecutiveBelowRecovery,
            alertAboveSeconds: ALERT_LAG_SECONDS,
            recoverBelowSeconds: TARGET_LAG_SECONDS,
            requiredConsecutiveEvaluations: REQUIRED_CONSECUTIVE_EVALUATIONS,
        },
        publication: {
            candidateOutputSha256: evaluation.outputSha256,
            selectedOutputSha256,
            state:
                selectedOutputSha256 === null
                    ? "missing"
                    : "retained-last-known-good",
            freshness: "not-evaluated",
        },
        metric: metricModel(evaluation, disposition),
        nextState: structuredClone(state),
    };
}

export function createReportingFreshnessState() {
    return {
        contract: "verdify.lab-reporting-freshness-state",
        schemaVersion: 1,
        revision: 0,
        samples: [],
        lastEvaluation: null,
        alertState: "inactive",
        consecutiveAboveAlert: 0,
        consecutiveBelowRecovery: 0,
        lastKnownGood: null,
    };
}

export function serializeReportingFreshnessState(state) {
    validateState(state);
    const serialized = canonicalStateText(state);
    if (Buffer.byteLength(serialized) > MAX_STATE_BYTES) {
        throw new Error("reporting freshness state exceeds its byte bound");
    }
    return serialized;
}

export function deserializeReportingFreshnessState(serialized) {
    if (typeof serialized !== "string" && !Buffer.isBuffer(serialized)) {
        throw new Error("serialized reporting freshness state is invalid");
    }
    const bytes = Buffer.isBuffer(serialized)
        ? serialized
        : Buffer.from(serialized);
    if (bytes.length < 1 || bytes.length > MAX_STATE_BYTES) {
        throw new Error(
            "serialized reporting freshness state exceeds its byte bound",
        );
    }
    let state;
    try {
        state = JSON.parse(bytes.toString("utf8"));
    } catch {
        throw new Error("serialized reporting freshness state is not JSON");
    }
    validateState(state);
    if (canonicalStateText(state) !== bytes.toString("utf8")) {
        throw new Error(
            "serialized reporting freshness state is not canonical",
        );
    }
    return structuredClone(state);
}

/**
 * Evaluate one injected source-lag observation. The caller owns clocks, metric
 * transport, persistence, and release selection; this function only returns a
 * bounded canonical next state and an opaque digest-level LKG decision.
 */
export function evaluateReportingFreshness({ state, evaluation }) {
    validateState(state);
    validateEvaluation(evaluation);
    const prior = structuredClone(state);

    if (prior.lastEvaluation !== null) {
        const comparison =
            Date.parse(evaluation.evaluatedAt) -
            Date.parse(prior.lastEvaluation.evaluatedAt);
        if (comparison < 0)
            return ignoredResult(prior, evaluation, "out-of-order");
        if (comparison === 0) {
            if (
                JSON.stringify(evaluation) !==
                JSON.stringify(prior.lastEvaluation)
            ) {
                throw new Error(
                    "duplicate reporting freshness evaluation conflicts with persisted state",
                );
            }
            return ignoredResult(prior, evaluation, "duplicate");
        }
    }

    const next = structuredClone(prior);
    next.revision += 1;
    next.lastEvaluation = structuredClone(evaluation);
    next.samples = pruneRollingWindow(next.samples, evaluation.evaluatedAt);
    if (
        prior.lastEvaluation !== null &&
        Date.parse(evaluation.evaluatedAt) -
            Date.parse(prior.lastEvaluation.evaluatedAt) >
            MAX_CONSECUTIVE_EVALUATION_GAP_SECONDS * 1000
    ) {
        next.consecutiveAboveAlert = 0;
        next.consecutiveBelowRecovery = 0;
    }
    let transition = "none";
    let alertEvaluation = "missing";

    let windowSampleDisposition = "not-scheduled";
    if (
        evaluation.lagSeconds !== null &&
        isQuarterHourAnchor(evaluation.evaluatedAt)
    ) {
        next.samples.push(structuredClone(evaluation));
        next.samples = next.samples.slice(-WINDOW_SAMPLE_COUNT);
        windowSampleDisposition = "appended";
    } else if (
        evaluation.lagSeconds === null &&
        isQuarterHourAnchor(evaluation.evaluatedAt)
    ) {
        windowSampleDisposition = "missing";
    }

    if (evaluation.lagSeconds === null) {
        next.consecutiveAboveAlert = 0;
        next.consecutiveBelowRecovery = 0;
    } else {
        if (evaluation.lagSeconds > ALERT_LAG_SECONDS) {
            alertEvaluation = "above-alert";
            next.consecutiveBelowRecovery = 0;
            next.consecutiveAboveAlert =
                next.alertState === "firing"
                    ? REQUIRED_CONSECUTIVE_EVALUATIONS
                    : Math.min(
                          REQUIRED_CONSECUTIVE_EVALUATIONS,
                          next.consecutiveAboveAlert + 1,
                      );
            if (
                next.alertState === "inactive" &&
                next.consecutiveAboveAlert === REQUIRED_CONSECUTIVE_EVALUATIONS
            ) {
                next.alertState = "firing";
                transition = "fired";
            }
        } else if (evaluation.lagSeconds < TARGET_LAG_SECONDS) {
            alertEvaluation = "below-recovery";
            next.consecutiveAboveAlert = 0;
            if (next.alertState === "firing") {
                next.consecutiveBelowRecovery = Math.min(
                    REQUIRED_CONSECUTIVE_EVALUATIONS,
                    next.consecutiveBelowRecovery + 1,
                );
                if (
                    next.consecutiveBelowRecovery ===
                    REQUIRED_CONSECUTIVE_EVALUATIONS
                ) {
                    next.alertState = "inactive";
                    next.consecutiveBelowRecovery = 0;
                    transition = "recovered";
                }
            } else {
                next.consecutiveBelowRecovery = 0;
            }
        } else {
            alertEvaluation = "neutral";
            next.consecutiveAboveAlert = 0;
            next.consecutiveBelowRecovery = 0;
        }
    }

    const window = windowModel(next.samples);
    const { status } = window;
    let publicationFreshness;
    if (next.alertState === "firing") publicationFreshness = "alert";
    else if (evaluation.lagSeconds === null || evaluation.outputSha256 === null)
        publicationFreshness = "missing";
    else if (evaluation.lagSeconds > ALERT_LAG_SECONDS || status === "stale")
        publicationFreshness = "stale";
    else if (evaluation.lagSeconds > TARGET_LAG_SECONDS || status === "late")
        publicationFreshness = "late";
    else if (status === "insufficient") publicationFreshness = "insufficient";
    else publicationFreshness = "fresh";

    if (publicationFreshness === "fresh") {
        next.lastKnownGood = {
            outputSha256: evaluation.outputSha256,
            verifiedAt: evaluation.evaluatedAt,
            lagSeconds: evaluation.lagSeconds,
        };
    }
    validateState(next);

    const selectedOutputSha256 = next.lastKnownGood?.outputSha256 ?? null;
    const selectedCandidate = publicationFreshness === "fresh";
    return {
        contract: "verdify.lab-reporting-freshness-evaluation",
        schemaVersion: 1,
        evaluatedAt: evaluation.evaluatedAt,
        sampleDisposition: "accepted",
        windowSampleDisposition,
        window,
        alert: {
            state: next.alertState,
            transition,
            evaluation: alertEvaluation,
            consecutiveAboveAlert: next.consecutiveAboveAlert,
            consecutiveBelowRecovery: next.consecutiveBelowRecovery,
            alertAboveSeconds: ALERT_LAG_SECONDS,
            recoverBelowSeconds: TARGET_LAG_SECONDS,
            requiredConsecutiveEvaluations: REQUIRED_CONSECUTIVE_EVALUATIONS,
        },
        publication: {
            candidateOutputSha256: evaluation.outputSha256,
            selectedOutputSha256,
            state:
                selectedOutputSha256 === null
                    ? "missing"
                    : selectedCandidate
                      ? "verified-fresh"
                      : "retained-last-known-good",
            freshness: publicationFreshness,
        },
        metric: metricModel(evaluation, "accepted"),
        nextState: next,
    };
}

export const reportingFreshnessContract = Object.freeze({
    metricName: METRIC_NAME,
    windowSampleCount: WINDOW_SAMPLE_COUNT,
    minimumSampleCount: WINDOW_SAMPLE_COUNT,
    windowSeconds: WINDOW_SECONDS,
    sampleCadenceSeconds: SAMPLE_CADENCE_SECONDS,
    sampleAnchor: SAMPLE_ANCHOR,
    completeWindowCoverageSeconds: COMPLETE_WINDOW_COVERAGE_SECONDS,
    windowAgeBoundary: WINDOW_AGE_BOUNDARY,
    maximumConsecutiveEvaluationGapSeconds:
        MAX_CONSECUTIVE_EVALUATION_GAP_SECONDS,
    targetLagSeconds: TARGET_LAG_SECONDS,
    alertLagSeconds: ALERT_LAG_SECONDS,
    requiredConsecutiveEvaluations: REQUIRED_CONSECUTIVE_EVALUATIONS,
    maximumLagSeconds: MAX_LAG_SECONDS,
    maximumStateBytes: MAX_STATE_BYTES,
});
