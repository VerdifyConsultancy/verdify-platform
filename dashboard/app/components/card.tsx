// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { useApiDown } from "../lib/session";
import { cls } from "./common";
import { HeadEnd, type HeadLink } from "./panel";

type CardBase = {
  title?: ReactNode; eyebrow?: ReactNode; headingLevel?: 2 | 3; className?: string; id?: string; children?: ReactNode;
  /** The one state pill of the head (redesign §4.4), after the title. */
  pill?: ReactNode;
  /** Abnormal: a 3 px `--amber` or `--fault` rule on the left; the background stays white. Dropped while the API is down. */
  tone?: "warn" | "fault";
  /** The foot: a `.meta` line or a "Details ›" link. */
  foot?: ReactNode;
};
/** A card with its own controls in the head: actions, an "All … ›" link and a ⋯ menu. */
type StaticCard = CardBase & { actions?: ReactNode; link?: HeadLink; menu?: ReactNode; to?: undefined };
/** An interactive card: the whole card is one link, so it holds no other control. */
type LinkCard = CardBase & { to: string; actions?: undefined; link?: undefined; menu?: undefined };

/**
 * Weather's `.card` (1px line, radius .625rem, xs shadow, 1rem padding), the static frame of redesign §4.1: an optional
 * eyebrow, the title with one pill, the head's actions, link and menu, the body and a meta foot. A data card that loads
 * is a `Panel`, which has the same head.
 */
export function Card(props: StaticCard | LinkCard) {
  const { title, eyebrow, headingLevel = 2, className, id, children, pill, tone, foot } = props;
  const headingId = useId();
  const apiDown = useApiDown();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const label = (eyebrow || title) && <div className="card-title">
    {eyebrow && <span className="eyebrow">{eyebrow}</span>}
    {title && <div className="card-title-row"><Heading id={headingId}>{title}</Heading>{pill}</div>}
  </div>;
  const classes = cls("card", tone && !apiDown && `tone-${tone}`, className);
  if (props.to !== undefined) {
    return <Link to={props.to} id={id} className={cls(classes, "card-link")}>{label && <div className="card-head">{label}</div>}{children}{foot && <div className="card-foot meta">{foot}</div>}</Link>;
  }
  const end = <HeadEnd actions={props.actions} link={props.link} menu={props.menu} />;
  const head = (label || props.actions || props.link || props.menu) && <div className="card-head">{label}{end}</div>;
  return <div id={id} className={classes}>{head}{children}{foot && <div className="card-foot meta">{foot}</div>}</div>;
}
