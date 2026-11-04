// SPDX-License-Identifier: MIT
import { useEffect, useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { usePhone } from "../lib/narrow";
import { cls } from "./common";
import { focusables } from "./forms/modal";

export type PopoverProps = {
  /** Open while the URL (or the screen) says so: `?run=` on a timeline block. */
  open: boolean;
  onClose: () => void;
  /** The element it hangs from on desktop, and where focus returns when it closes. */
  anchor: HTMLElement | null;
  /** Its heading: "L01 Wall drip · Wall MWF". */
  title: ReactNode;
  children?: ReactNode;
  className?: string;
};

const GAP = 6;
const EDGE = 8;

// jsdom has no show(), showModal() or close(); the open attribute stands in for them.
function showAs(dialog: HTMLDialogElement, modal: boolean) {
  if (dialog.open) return;
  const method = modal ? dialog.showModal : dialog.show;
  if (typeof method === "function") method.call(dialog); else dialog.setAttribute("open", "");
}
function hide(dialog: HTMLDialogElement) {
  if (typeof dialog.close === "function") { if (dialog.open) dialog.close(); } else dialog.removeAttribute("open");
}

/** Under the anchor's left edge, kept inside the viewport; set as CSSOM custom properties, never a style attribute. */
function place(dialog: HTMLDialogElement, anchor: HTMLElement | null) {
  const view = dialog.ownerDocument.defaultView;
  if (!anchor || !view) return;
  const box = anchor.getBoundingClientRect();
  const width = dialog.offsetWidth;
  const left = Math.min(Math.max(box.left, EDGE), Math.max(EDGE, view.innerWidth - width - EDGE)) + view.scrollX;
  dialog.style.setProperty("--pop-top", `${Math.round(box.bottom + GAP + view.scrollY)}px`);
  dialog.style.setProperty("--pop-left", `${Math.round(left)}px`);
}

/**
 * Popover (redesign §5.14): a block's or a cell's detail. From 861 px it hangs under its opener as a non-modal
 * `<dialog>` that a press outside closes; at 860 px and below it is a bottom sheet in a modal `<dialog>` over a dimmed
 * page. Either way focus moves into it and returns to the opener, and Escape closes it. It lives in the URL where its
 * screen keeps it (push to open, replace to close), so Back closes it. Its markup carries no style attribute.
 */
export function Popover({ open, onClose, anchor, title, children, className }: PopoverProps) {
  const phone = usePhone();
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const doc = dialog.ownerDocument;
    const opener = anchor ?? (doc.activeElement instanceof HTMLElement ? doc.activeElement : null);
    showAs(dialog, phone);
    if (!phone) place(dialog, anchor);
    (focusables(dialog)[0] ?? dialog).focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeRef.current();
    };
    const onPress = (event: Event) => {
      const target = event.target as Node;
      if (!dialog.contains(target) && !anchor?.contains(target)) closeRef.current();
    };
    dialog.addEventListener("keydown", onKey);
    if (!phone) doc.addEventListener("pointerdown", onPress);
    return () => {
      dialog.removeEventListener("keydown", onKey);
      doc.removeEventListener("pointerdown", onPress);
      hide(dialog);
      if (opener?.isConnected) opener.focus();
    };
  }, [open, phone, anchor]);

  if (!open || typeof document === "undefined") return null;
  return createPortal(<dialog ref={ref} className={cls("popover", phone ? "popover-sheet" : "popover-anchored", className)} aria-labelledby={headingId} tabIndex={-1}
    onCancel={(event) => { event.preventDefault(); closeRef.current(); }}>
    <div className="popover-head">
      <h2 id={headingId}>{title}</h2>
      <button type="button" className="popover-close" aria-label="Close" onClick={() => closeRef.current()}><span aria-hidden="true">×</span></button>
    </div>
    <div className="popover-body">{children}</div>
  </dialog>, document.body);
}
