// SPDX-License-Identifier: MIT
import { BandEditor } from "../components/control/band-editor";
import { PageHeader } from "../components/layout/page-header";
import { Panel } from "../components/panel";
import { chartLinks } from "../charts/uplot-chart";
import { viewMeta } from "../lib/sections";

/**
 * Control → Band (#1334): what the climate band is and how it moves through the day, the house band's editor and its
 * 24 h preview. Plant-agnostic: the band is a temperature and a VPD target, never a crop. docs/design/dashboard/band.md
 * is the long form of the explainer below. No greenhouse data renders in SSR: data loads in the browser.
 */
export const meta = () => viewMeta("control-band");
export const links = () => chartLinks();

export default function Band() {
  return <>
    <PageHeader view="control-band" status="The house band: temperature and VPD targets through the solar day" />
    <BandEditor />
    <Panel title="How the band works" thing="the explainer" state="ready">
      <div className="prose">
        <p>The band is the range the controller holds the house in: a low, a target and a high for temperature and for VPD. It is set at four points of the sun's day (sunrise, solar noon, sunset and solar midnight) and the controller draws one smooth curve through them, so the band follows the season without fixed clock times.</p>
        <p>Here you set the day value (solar noon) and the night value (solar midnight). Sunrise and sunset are placed halfway between, so the curve never runs past the values you set. Advanced shows the 24 raw points; uneven sunrise and sunset values make the curve overshoot, which the preview shows.</p>
        <p>Below the temperature low the controller heats (electric heat first, gas as backup); above the high it cools. Below the VPD low it dries the air; above the VPD high it humidifies. The hard guardrails, 40 °F and 100 °F, sit above all of this. At night the planner may add a small dry-out bias to VPD (dotted on the chart); the band itself is set only by an operator, never by the planner.</p>
        <p>Save writes every changed point as one change with one reason. The controller picks it up within a couple of dispatcher cycles; Settings shows each point as REQUESTED until the controller confirms it.</p>
      </div>
    </Panel>
  </>;
}
