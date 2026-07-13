import assert from "node:assert/strict";
import test from "node:test";

import {
    createReportingFreshnessState,
    deserializeReportingFreshnessState,
    evaluateReportingFreshness,
    reportingFreshnessContract,
    serializeReportingFreshnessState,
} from "../scripts/lib/reporting-freshness-state.mjs";

const START = Date.parse("2026-07-13T00:00:00Z");

function instant(index) {
    return new Date(START + index * 15 * 60 * 1000).toISOString();
}

function digest(index) {
    return (index + 1).toString(16).padStart(64, "0");
}

function evaluation(
    index,
    lagSeconds,
    outputSha256 = lagSeconds === null ? null : digest(index),
) {
    return { evaluatedAt: instant(index), lagSeconds, outputSha256 };
}

function apply(state, index, lagSeconds, outputSha256) {
    return evaluateReportingFreshness({
        state,
        evaluation: evaluation(index, lagSeconds, outputSha256),
    });
}

function seed({ count = 96, lagSeconds = 100, startIndex = 0 } = {}) {
    let state = createReportingFreshnessState();
    let result = null;
    for (let offset = 0; offset < count; offset += 1) {
        result = apply(state, startIndex + offset, lagSeconds);
        state = result.nextState;
    }
    return { state, result, nextIndex: startIndex + count };
}

test("rolling nearest-rank p95 requires 96 samples and discards the oldest sample", () => {
    assert.equal(reportingFreshnessContract.windowSeconds, 86_400);
    assert.equal(reportingFreshnessContract.sampleCadenceSeconds, 900);
    assert.equal(
        reportingFreshnessContract.windowAgeBoundary,
        "(evaluatedAt-86400s,evaluatedAt]",
    );
    let state = createReportingFreshnessState();
    let result;
    for (let index = 0; index < 95; index += 1) {
        result = apply(state, index, index + 1);
        state = result.nextState;
    }
    assert.equal(result.window.sampleCount, 95);
    assert.equal(result.window.minimumSampleCount, 96);
    assert.equal(result.window.windowSeconds, 86_400);
    assert.equal(result.window.sampleCadenceSeconds, 900);
    assert.equal(result.window.ageBoundary, "(evaluatedAt-86400s,evaluatedAt]");
    assert.equal(result.window.coverageSeconds, 84_600);
    assert.equal(result.window.cadenceValid, true);
    assert.equal(result.window.p95LagSeconds, 91);
    assert.equal(result.window.status, "insufficient");
    assert.deepEqual(result.publication, {
        candidateOutputSha256: digest(94),
        selectedOutputSha256: null,
        state: "missing",
        freshness: "insufficient",
    });

    result = apply(state, 95, 96);
    state = result.nextState;
    assert.equal(result.window.sampleCount, 96);
    assert.equal(result.window.coverageSeconds, 85_500);
    assert.equal(result.window.cadenceValid, true);
    assert.equal(result.window.p95LagSeconds, 92);
    assert.equal(result.window.status, "target");
    assert.equal(result.publication.state, "verified-fresh");
    assert.equal(result.publication.selectedOutputSha256, digest(95));

    result = apply(state, 96, 97);
    assert.equal(result.window.sampleCount, 96);
    assert.equal(result.window.p95LagSeconds, 93);
    assert.equal(result.nextState.samples[0].evaluatedAt, instant(1));
    assert.equal(result.nextState.samples.at(-1).evaluatedAt, instant(96));
});

test("24-hour age and fifteen-minute cadence gates prevent gaps or dense samples from looking fresh", () => {
    const seeded = seed();
    const initialLkg = seeded.result.publication.selectedOutputSha256;
    const afterGapIndex = seeded.nextIndex - 1 + 26 * 4;
    let result = apply(seeded.state, afterGapIndex, 100);
    let state = result.nextState;
    assert.equal(result.window.sampleCount, 1);
    assert.equal(result.window.coverageSeconds, 0);
    assert.equal(result.window.status, "insufficient");
    assert.equal(result.publication.state, "retained-last-known-good");
    assert.equal(result.publication.selectedOutputSha256, initialLkg);
    assert.equal(result.publication.freshness, "insufficient");

    for (let offset = 1; offset < 96; offset += 1) {
        result = apply(state, afterGapIndex + offset, 100);
        state = result.nextState;
    }
    assert.equal(result.window.sampleCount, 96);
    assert.equal(result.window.coverageSeconds, 85_500);
    assert.equal(result.window.cadenceValid, true);
    assert.equal(result.window.status, "target");
    assert.equal(result.publication.state, "verified-fresh");

    state = createReportingFreshnessState();
    for (let index = 0; index < 96; index += 1) {
        result = evaluateReportingFreshness({
            state,
            evaluation: {
                evaluatedAt: new Date(
                    START + index * 10 * 60 * 1000,
                ).toISOString(),
                lagSeconds: 100,
                outputSha256: digest(index),
            },
        });
        state = result.nextState;
    }
    assert.equal(result.window.sampleCount, 96);
    assert.equal(result.window.coverageSeconds, 57_000);
    assert.equal(result.window.cadenceValid, false);
    assert.equal(result.window.status, "insufficient");
    assert.equal(result.publication.state, "missing");
});

test("strict alert and recovery boundaries require two consecutive evaluations", () => {
    let { state, nextIndex: index } = seed();

    let result = apply(state, index, 1800);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.evaluation, "neutral");
    assert.equal(result.alert.state, "inactive");
    assert.equal(result.publication.freshness, "late");

    result = apply(state, index, 1801);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.consecutiveAboveAlert, 1);
    assert.equal(result.alert.state, "inactive");
    assert.equal(result.publication.freshness, "stale");

    result = apply(state, index, 1801);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.transition, "fired");
    assert.equal(result.alert.state, "firing");
    assert.equal(result.publication.freshness, "alert");

    result = apply(state, index, 900);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.evaluation, "neutral");
    assert.equal(result.alert.state, "firing");
    assert.equal(result.alert.consecutiveBelowRecovery, 0);

    result = apply(state, index, 899);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.state, "firing");
    assert.equal(result.alert.consecutiveBelowRecovery, 1);

    result = apply(state, index, 899);
    assert.equal(result.alert.transition, "recovered");
    assert.equal(result.alert.state, "inactive");
    assert.equal(result.publication.freshness, "fresh");
    assert.equal(result.publication.state, "verified-fresh");

    const separated = seed();
    const firstSeparatedHigh = apply(
        separated.state,
        separated.nextIndex,
        1801,
    );
    const highAfterGap = apply(
        firstSeparatedHigh.nextState,
        separated.nextIndex + 2,
        1801,
    );
    assert.equal(
        reportingFreshnessContract.maximumConsecutiveEvaluationGapSeconds,
        900,
    );
    assert.equal(highAfterGap.alert.state, "inactive");
    assert.equal(highAfterGap.alert.consecutiveAboveAlert, 1);
});

test("missing, duplicate, and out-of-order observations never advance the window or hysteresis", () => {
    let { state, nextIndex: index } = seed();
    let result = apply(state, index, 1801);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.consecutiveAboveAlert, 1);
    const revisionBeforeMissing = state.revision;

    const missing = evaluation(index, null);
    result = evaluateReportingFreshness({ state, evaluation: missing });
    state = result.nextState;
    assert.equal(result.sampleDisposition, "accepted");
    assert.equal(result.window.sampleCount, 95);
    assert.equal(result.window.status, "insufficient");
    assert.equal(result.nextState.samples[0].evaluatedAt, instant(2));
    assert.equal(result.alert.consecutiveAboveAlert, 0);
    assert.equal(result.metric.sample, null);
    assert.equal(state.revision, revisionBeforeMissing + 1);

    const duplicate = evaluateReportingFreshness({
        state,
        evaluation: missing,
    });
    assert.equal(duplicate.sampleDisposition, "duplicate");
    assert.equal(duplicate.nextState.revision, state.revision);
    assert.equal(duplicate.metric.sample, null);

    const outOfOrder = apply(state, index - 1, 1801);
    assert.equal(outOfOrder.sampleDisposition, "out-of-order");
    assert.deepEqual(outOfOrder.nextState, state);

    assert.throws(
        () => apply(state, index, 100),
        /duplicate reporting freshness evaluation conflicts/,
    );

    index += 1;
    result = apply(state, index, 1801);
    state = result.nextState;
    assert.equal(result.alert.consecutiveAboveAlert, 1);
    result = apply(state, index + 1, 1801);
    assert.equal(result.alert.transition, "fired");
});

test("serialized restart preserves alert hysteresis and LKG without relabelling bad output", () => {
    let { state, result: seeded, nextIndex: index } = seed();
    const initialLkg = seeded.publication.selectedOutputSha256;
    assert.equal(seeded.publication.state, "verified-fresh");

    for (let offset = 0; offset < 5; offset += 1) {
        const result = apply(state, index, 1801);
        state = result.nextState;
        index += 1;
    }
    assert.equal(state.alertState, "firing");
    assert.equal(state.lastKnownGood.outputSha256, initialLkg);

    const serialized = serializeReportingFreshnessState(state);
    assert.ok(
        Buffer.byteLength(serialized) <
            reportingFreshnessContract.maximumStateBytes,
    );
    state = deserializeReportingFreshnessState(Buffer.from(serialized));

    let result = apply(state, index, 899);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.state, "firing");
    assert.equal(result.publication.state, "retained-last-known-good");
    assert.equal(result.publication.selectedOutputSha256, initialLkg);
    assert.equal(result.publication.freshness, "alert");

    state = deserializeReportingFreshnessState(
        serializeReportingFreshnessState(state),
    );
    result = apply(state, index, 899);
    state = result.nextState;
    index += 1;
    assert.equal(result.alert.transition, "recovered");
    assert.equal(result.window.status, "stale");
    assert.equal(result.publication.state, "retained-last-known-good");
    assert.equal(result.publication.selectedOutputSha256, initialLkg);
    assert.equal(result.publication.freshness, "stale");

    for (let offset = 0; offset < 90; offset += 1) {
        result = apply(state, index, 100);
        state = result.nextState;
        index += 1;
    }
    assert.equal(result.window.status, "target");
    assert.equal(result.publication.state, "verified-fresh");
    assert.notEqual(result.publication.selectedOutputSha256, initialLkg);
});

test("restart rejects streaks that cannot follow the persisted evaluation and alert state", () => {
    const canonical = (state) => `${JSON.stringify(state, null, 2)}\n`;
    const rejectsInconsistent = (state) =>
        assert.throws(
            () => deserializeReportingFreshnessState(canonical(state)),
            /hysteresis is inconsistent with its last evaluation/,
        );

    assert.throws(
        () =>
            deserializeReportingFreshnessState(
                canonical({
                    ...createReportingFreshnessState(),
                    alertState: "firing",
                }),
            ),
        /hysteresis is unreachable without an evaluation/,
    );

    const seeded = seed();
    assert.throws(
        () =>
            deserializeReportingFreshnessState(
                canonical({
                    ...seeded.state,
                    revision: seeded.state.revision + 1,
                    lastEvaluation: evaluation(seeded.nextIndex, null),
                }),
            ),
        /outside the rolling 24-hour window/,
    );
    rejectsInconsistent({
        ...seeded.state,
        consecutiveAboveAlert: 1,
    });

    const firstHigh = apply(seeded.state, seeded.nextIndex, 1801).nextState;
    assert.equal(firstHigh.alertState, "inactive");
    assert.equal(firstHigh.consecutiveAboveAlert, 1);
    rejectsInconsistent({ ...firstHigh, consecutiveAboveAlert: 0 });

    const firing = apply(firstHigh, seeded.nextIndex + 1, 1801).nextState;
    assert.equal(firing.alertState, "firing");
    assert.equal(firing.consecutiveAboveAlert, 2);
    rejectsInconsistent({ ...firing, consecutiveAboveAlert: 1 });

    const recovering = apply(firing, seeded.nextIndex + 2, 899).nextState;
    assert.equal(recovering.consecutiveBelowRecovery, 1);
    rejectsInconsistent({ ...recovering, consecutiveBelowRecovery: 0 });

    const neutral = apply(firing, seeded.nextIndex + 2, 900).nextState;
    assert.equal(neutral.consecutiveAboveAlert, 0);
    assert.equal(neutral.consecutiveBelowRecovery, 0);
    rejectsInconsistent({ ...neutral, consecutiveBelowRecovery: 1 });

    const missing = evaluateReportingFreshness({
        state: firstHigh,
        evaluation: evaluation(seeded.nextIndex + 1, null),
    }).nextState;
    assert.equal(missing.lastEvaluation.lagSeconds, null);
    rejectsInconsistent({ ...missing, consecutiveAboveAlert: 1 });
});

test("metric and persisted output are bounded, single-series, and value-only", () => {
    let { state, nextIndex: index } = seed();
    const result = apply(state, index, 123);
    assert.deepEqual(result.metric, {
        contract: "verdify.lab-reporting-source-lag-metric",
        schemaVersion: 1,
        name: "verdify_lab_reporting_source_lag_seconds",
        type: "gauge",
        unit: "seconds",
        help: "Lag between the Lab reporting source watermark and its evaluation.",
        maximumSeries: 1,
        labels: {},
        sample: { observedAt: instant(index), value: 123 },
    });
    const output = JSON.stringify(result);
    assert.doesNotMatch(
        output,
        /https?:|endpoint|credential|authorization|cookie|token|password|secret/iu,
    );

    assert.throws(
        () =>
            evaluateReportingFreshness({
                state,
                evaluation: { ...evaluation(index, 123), endpoint: "redacted" },
            }),
        /closed v1 shape/,
    );
    assert.throws(
        () =>
            apply(
                state,
                index,
                reportingFreshnessContract.maximumLagSeconds + 1,
            ),
        /lag is invalid/,
    );
    assert.throws(
        () =>
            deserializeReportingFreshnessState(
                `${JSON.stringify(
                    {
                        ...state,
                        credential: "redacted",
                    },
                    null,
                    2,
                )}\n`,
            ),
        /closed v1 shape/,
    );
    assert.throws(
        () =>
            deserializeReportingFreshnessState(
                "x".repeat(reportingFreshnessContract.maximumStateBytes + 1),
            ),
        /byte bound/,
    );
});
