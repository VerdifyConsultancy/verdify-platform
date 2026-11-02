// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { cls } from "./common";

type CardBase = { title?: ReactNode; eyebrow?: ReactNode; headingLevel?: 2 | 3; className?: string; id?: string; children?: ReactNode };
/** A card with its own controls in the head. */
type StaticCard = CardBase & { actions?: ReactNode; to?: undefined };
/** An interactive card: the whole card is one link, so it holds no other control. */
type LinkCard = CardBase & { to: string; actions?: undefined };

/** Weather's `.card` (1px line, radius .625rem, xs shadow, 1rem padding) with an optional `.card-head`. */
export function Card(props: StaticCard | LinkCard) {
  const { title, eyebrow, headingLevel = 2, className, id, children } = props;
  const headingId = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const label = (eyebrow || title) && <div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}{title && <Heading id={headingId}>{title}</Heading>}</div>;
  if (props.to !== undefined) {
    return <Link to={props.to} id={id} className={cls("card", "card-link", className)}>{label && <div className="card-head">{label}</div>}{children}</Link>;
  }
  const head = (label || props.actions) && <div className="card-head">{label}{props.actions}</div>;
  return <div id={id} className={cls("card", className)}>{head}{children}</div>;
}
