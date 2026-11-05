// SPDX-License-Identifier: MIT
import { useEffect, useRef, type ReactNode } from "react";
import { cls } from "./common";

/**
 * Closes an open `<details>` the way a menu closes (redesign §4.5): Escape (focus returns to its summary), a press
 * outside it, and choosing a link or button inside its panel (an item marked `data-menu-keep` keeps it open, for a
 * switch that toggles in place). Choosing an item puts focus on the summary before the item acts, so a drawer or dialog
 * it opens finds the menu's button as its opener and returns focus there when it closes (redesign §8): the item itself is
 * hidden by then. Native `<details>` does the opening, so the menu works before hydration too.
 */
export function useDismissable(ref: { readonly current: HTMLDetailsElement | null }) {
  useEffect(() => {
    const details = ref.current;
    if (!details) return;
    const doc = details.ownerDocument;
    const summary = () => details.querySelector<HTMLElement>(":scope > summary");
    const close = (focus: boolean) => {
      if (!details.open) return;
      details.open = false;
      if (focus) summary()?.focus();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !details.open) return;
      event.preventDefault();
      close(true);
    };
    const onPress = (event: Event) => {
      if (details.open && !details.contains(event.target as Node)) close(false);
    };
    const onChoose = (event: Event) => {
      const item = (event.target as Element | null)?.closest?.("a[href], button");
      if (!item || item.closest("summary") || item.closest("[data-menu-keep]") || !details.contains(item)) return;
      close(true);
    };
    details.addEventListener("keydown", onKey);
    details.addEventListener("click", onChoose);
    doc.addEventListener("pointerdown", onPress);
    return () => {
      details.removeEventListener("keydown", onKey);
      details.removeEventListener("click", onChoose);
      doc.removeEventListener("pointerdown", onPress);
    };
  }, [ref]);
}

export type MenuProps = {
  /** What a `kind="button"` menu's button shows (the account's role pill and name); a dots menu shows "⋯". */
  label?: ReactNode;
  /** A dots menu's name for screen readers ("More for L01 Wall drip"), as text inside its button. */
  name?: string;
  /** Links and buttons, each a 44 px item; a viewer's menu keeps its links and has no write control. */
  children?: ReactNode;
  /** The panel's edge: under the button's right edge (default) or its left. */
  align?: "end" | "start";
  /** "dots" draws the ⋯ button without the disclosure chevron. */
  kind?: "dots" | "button";
  className?: string;
};

/**
 * The ⋯ menu of a row or card (redesign §4.5), and the account menu: a disclosure, never an ARIA menu, holding links
 * and buttons. The panel floats under its button at `--z-popover`; Escape, a press outside it or choosing an item closes
 * it. A card's emergency action (a line's Stop) never goes in here.
 */
export function Menu({ label, name, children, align = "end", kind = "dots", className }: MenuProps) {
  const ref = useRef<HTMLDetailsElement>(null);
  useDismissable(ref);
  return <details ref={ref} className={cls("menu", kind === "dots" && "menu-dots", align === "start" && "menu-start", className)}>
    <summary className="menu-button">{kind === "dots" ? <><span className="glyph glyph-more" aria-hidden="true" />{name && <span className="screen-reader">{name}</span>}</> : label}</summary>
    <div className="menu-panel">{children}</div>
  </details>;
}
