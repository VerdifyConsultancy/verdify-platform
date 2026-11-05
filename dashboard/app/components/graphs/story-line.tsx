// SPDX-License-Identifier: MIT
import { Fragment } from "react";
import { Link } from "react-router";
import { STORY_EXCURSIONS, excursionRange, excursionWords, shareWords, type Story } from "../../lib/graph-story";
import { exactWindow, graphHref, withWindow, type GraphView } from "../../lib/graph-url";

export type StoryLineProps = {
  /** "Last 24 h", "Today", "Oct 31–Nov 1": what the window is. */
  lead: string;
  story: Story;
  /** The view as the page draws it: each excursion's link keeps everything but the window. */
  view: GraphView;
  /** The window spans more than one local day, so times carry their date. */
  withDates: boolean;
  /**
   * The phone's wording (redesign §5.5): "24 h · in band 96% · 2 excursions ›", the count a link to the worst one's
   * stretch, so the line stays one line where the full one would take three.
   */
  short?: boolean;
};

/**
 * The story line (redesign §5.5), Climate's status line: the window, its time in band and its excursions, worst first,
 * at most three, each a link to its stretch with half an hour either side, the rest counted. Without a band in the
 * window it says so and grades nothing.
 */
export function StoryLine({ lead, story, view, withDates, short = false }: StoryLineProps) {
  const count = story.excursions.length;
  if (short) {
    const span = lead.replace(/^Last /, "");
    if (story.inBand === null) return <span className="gr-story">{span} · no band in this window</span>;
    const worst = story.excursions[0];
    const range = worst ? excursionRange(worst) : null;
    return <span className="gr-story">
      {span} · in band {shareWords(story.inBand)} · {worst && range
        ? <Link to={graphHref(withWindow(view, exactWindow(range.from, range.to)))}>
          {count} {count === 1 ? "excursion" : "excursions"}<span className="screen-reader">, the worst {excursionWords(worst, withDates)}</span><span aria-hidden="true"> ›</span>
        </Link>
        : "no excursions"}
    </span>;
  }
  if (story.inBand === null) return <span className="gr-story">{lead}: no band to grade against in this window</span>;
  const named = story.excursions.slice(0, STORY_EXCURSIONS);
  const rest = count - named.length;
  return <span className="gr-story">
    {lead}: in band {shareWords(story.inBand)} of points · {count === 0 ? "no excursions" : <>
      {count} {count === 1 ? "excursion" : "excursions"}: {named.map((excursion, index) => {
        const range = excursionRange(excursion);
        return <Fragment key={`${excursion.metric}-${excursion.start}`}>
          {index > 0 && ", "}
          <Link to={graphHref(withWindow(view, exactWindow(range.from, range.to)))}>{excursionWords(excursion, withDates)}</Link>
        </Fragment>;
      })}{rest > 0 && `, and ${rest} more`}
    </>}
  </span>;
}
