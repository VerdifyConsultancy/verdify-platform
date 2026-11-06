// SPDX-License-Identifier: MIT
import { cls } from "../common";
import type { MetricResult } from "../../lib/operator-api";
import { VERDICT_WORDS, ciDomain, estimateWords, verdict } from "../../lib/planner";

const WIDTH = 240;
const HEIGHT = 24;
const MID = HEIGHT / 2;

/**
 * One dot-and-whisker row per metric (#1361): AI minus baseline with its 95% confidence interval, drawn on the row's
 * own symmetric axis around zero (each metric has its own unit), a guardrail's non-inferiority margin as a dashed rule,
 * the numbers in words beside it and what the interval says. The figure is decorative: the words carry every number,
 * so a screen reader hears the same. Numbers come only from the analyzer's frozen output.
 */
export function CiRow({ metric, margin = null }: { metric: MetricResult; margin?: number | null }) {
  const said = verdict(metric, margin);
  const domain = ciDomain(metric, margin);
  const x = (value: number) => WIDTH / 2 + (value / domain) * (WIDTH / 2 - 8);
  const drawn = metric.estimate != null && metric.ci_low != null && metric.ci_high != null;
  return <li className="pln-ci-row" data-metric={metric.id} data-verdict={said}>
    <span className="pln-ci-label">{metric.label}{metric.role === "primary" && <span className="pln-ci-role"> · primary</span>}
      {metric.role === "guardrail" && <span className="pln-ci-role"> · guardrail</span>}</span>
    <svg className="pln-ci-plot" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width={WIDTH} height={HEIGHT} aria-hidden="true" focusable="false">
      <line className="pln-ci-zero" x1={x(0)} x2={x(0)} y1={2} y2={HEIGHT - 2} />
      {margin !== null && metric.role === "guardrail" && <line className="pln-ci-margin" x1={x(-margin)} x2={x(-margin)} y1={2} y2={HEIGHT - 2} />}
      {drawn && <>
        <line className={cls("pln-ci-whisker", `is-${said}`)} x1={x(metric.ci_low!)} x2={x(metric.ci_high!)} y1={MID} y2={MID} />
        <circle className={cls("pln-ci-dot", `is-${said}`)} cx={x(metric.estimate!)} cy={MID} r={4.5} />
      </>}
    </svg>
    <span className="pln-ci-words"><span className="mono">{estimateWords(metric)}</span> <span className={cls("pln-ci-verdict", `is-${said}`)}>{VERDICT_WORDS[said]}</span></span>
  </li>;
}

/** The results' rows in the definition's order: the primary, the guardrail, then the rest. */
export function CiRows({ metrics, margins }: { metrics: readonly MetricResult[]; margins: Readonly<Record<string, number | null>> }) {
  return <ul className="pln-ci-rows" aria-label="AI minus baseline, with 95% confidence intervals">
    {metrics.map((metric) => <CiRow key={metric.id} metric={metric} margin={margins[metric.id] ?? null} />)}
  </ul>;
}
