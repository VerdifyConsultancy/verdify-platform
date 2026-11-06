// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { Setting } from "../../lib/operator-api";
import { PanelError } from "../panel";
import { groupLabel } from "../setting-row";
import { lowerWord, raiseWord, readOnlyWords } from "./setting-help";
import { groupRows } from "./settings-catalog";

/**
 * The glossary (`/settings?view=glossary`, #1332): every setting the filter and search keep, by group, with its full
 * text: what it is, what it changes, raising and lowering it, the caution, why it is read-only and what it is computed
 * from. The words are API-09's `help`, the same MCP and the lab reference page serve. It prints as it reads.
 */
export function SettingsGlossary({ rows, failed, onRetry, editTo, labelOf, empty }: {
  rows: readonly Setting[] | null;
  failed: boolean;
  onRetry: () => void;
  editTo: (param: string) => string;
  labelOf: (param: string) => string;
  empty: ReactNode;
}) {
  if (rows === null) {
    return <section className="ctl-glossary" aria-label="Settings glossary" aria-busy={!failed ? true : undefined}>
      {failed ? <PanelError thing="settings" onRetry={onRetry} /> : <p className="screen-reader">Loading settings</p>}
    </section>;
  }
  if (rows.length === 0) return <section className="ctl-glossary ctl-catalog-empty" aria-label="Settings glossary">{empty}</section>;
  return <section className="ctl-glossary" aria-label="Settings glossary">
    {groupRows(rows).map(([group, list]) => <section key={group} className="ctl-glossary-group" aria-labelledby={`glossary-${group}`}>
      <h2 id={`glossary-${group}`} className="ctl-glossary-head">{groupLabel(group)} <span className="table-group-count">· {list.length}</span></h2>
      {list.map((row) => <GlossaryEntry key={row.param} setting={row} editTo={editTo} labelOf={labelOf} />)}
    </section>)}
  </section>;
}

function GlossaryEntry({ setting, editTo, labelOf }: { setting: Setting; editTo: (param: string) => string; labelOf: (param: string) => string }) {
  const help = setting.help;
  const readOnly = readOnlyWords(setting);
  const sources = setting.source_params ?? [];
  return <article className="ctl-glossary-entry" data-param={setting.param}>
    <h3><Link to={editTo(setting.param)} preventScrollReset>{setting.label}</Link> <span className="setting-param mono">{setting.param}</span></h3>
    {help ? <p className="ctl-glossary-summary">{help.summary}</p> : <p className="state-note">No description served</p>}
    <dl className="ctl-help-list">
      {help && <div><dt>What it changes</dt><dd>{help.effect}</dd></div>}
      {help?.raise && <div><dt>{raiseWord(setting.kind)}</dt><dd>{help.raise}</dd></div>}
      {help?.lower && <div><dt>{lowerWord(setting.kind)}</dt><dd>{help.lower}</dd></div>}
      {help?.caution && <div><dt>{help.guardrail ? "Guardrail" : "Caution"}</dt><dd>{help.caution}</dd></div>}
      {readOnly && <div><dt>Read-only</dt><dd>{readOnly.label}{readOnly.definition !== readOnly.label && <>: {readOnly.definition}</>}</dd></div>}
      {sources.length > 0 && <div><dt>Computed from</dt><dd>{sources.map((param, index) => <span key={param}>{index > 0 && ", "}<Link to={editTo(param)} preventScrollReset>{labelOf(param)}</Link></span>)}</dd></div>}
    </dl>
  </article>;
}
