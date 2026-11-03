// SPDX-License-Identifier: MIT
import { useCallback, useEffect, useRef, type KeyboardEvent, type RefObject, type SyntheticEvent } from "react";

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled]):not([type=hidden])", "select:not([disabled])", "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");
const FIELD = "input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled])";

/** The controls Tab can reach inside `root`, in document order (a control in a disabled fieldset is disabled). */
export function focusables(root: ParentNode): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((element) => !element.closest("fieldset:disabled") && !element.hasAttribute("hidden"));
}
/** The first form field inside `root` that takes input, else null. */
export function firstField(root: ParentNode): HTMLElement | null {
  return [...root.querySelectorAll<HTMLElement>(FIELD)].find((element) => !element.closest("fieldset:disabled")) ?? null;
}

// jsdom has no showModal() or close(); the open attribute then stands in for them.
function show(dialog: HTMLDialogElement) {
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
  } else dialog.setAttribute("open", "");
}
function hide(dialog: HTMLDialogElement) {
  if (typeof dialog.close === "function") {
    if (dialog.open) dialog.close();
  } else dialog.removeAttribute("open");
}

/**
 * A native modal `<dialog>` driven by `open` (design system §7): `showModal()` when it opens, focus on `initialFocus`
 * (else the first control), and back to the opener when it closes or unmounts. Escape and the dialog's own cancel close
 * it at once, without asking; Tab and Shift+Tab wrap inside it, so focus never leaves while it is open.
 */
export function useModalDialog(ref: RefObject<HTMLDialogElement | null>, open: boolean, onClose: () => void, initialFocus?: (dialog: HTMLDialogElement) => HTMLElement | null) {
  const closeRef = useRef(onClose);
  const focusRef = useRef(initialFocus);
  useEffect(() => { closeRef.current = onClose; focusRef.current = initialFocus; });
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const opener = dialog.ownerDocument.activeElement instanceof HTMLElement && !dialog.contains(dialog.ownerDocument.activeElement) ? dialog.ownerDocument.activeElement : null;
    show(dialog);
    (focusRef.current?.(dialog) ?? focusables(dialog)[0] ?? dialog).focus();
    return () => {
      hide(dialog);
      if (opener?.isConnected) opener.focus();
    };
  }, [open, ref]);
  const onKeyDown = useCallback((event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeRef.current();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables(event.currentTarget);
    if (!items.length) { event.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    const active = event.currentTarget.ownerDocument.activeElement;
    if (event.shiftKey && (active === first || !event.currentTarget.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !event.currentTarget.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }, []);
  // The native cancel (Escape) would close the element behind React's back; the screen's state closes it instead.
  const onCancel = useCallback((event: SyntheticEvent<HTMLDialogElement>) => {
    event.preventDefault();
    closeRef.current();
  }, []);
  return { onKeyDown, onCancel };
}
