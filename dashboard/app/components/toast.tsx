// SPDX-License-Identifier: MIT
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cls } from "./common";

/** A success toast leaves after this long, unless it carries guidance or buttons. */
export const SUCCESS_TOAST_MS = 5000;

export type ToastOptions = {
  /** success (polite) or failure (`role="alert"`, stays until dismissed). */
  kind?: "success" | "failure";
  message: ReactNode;
  /** Guidance under the message; a toast with guidance stays until dismissed. */
  detail?: ReactNode;
  /** Buttons, such as Stop's Disable (#1125); a toast with buttons stays until dismissed. */
  actions?: ReactNode;
  /** Stays until dismissed even without guidance or buttons. */
  sticky?: boolean;
};
type ToastEntry = ToastOptions & { id: number; kind: "success" | "failure" };
export type ToastApi = { show: (toast: ToastOptions) => number; dismiss: (id: number) => void };

const ToastContext = createContext<ToastApi | null>(null);
const stays = (toast: ToastEntry) => toast.kind === "failure" || Boolean(toast.sticky || toast.detail || toast.actions);

function Toast({ toast, onDismiss }: { toast: ToastEntry; onDismiss: (id: number) => void }) {
  const sticky = stays(toast);
  useEffect(() => {
    if (sticky) return;
    const timer = setTimeout(() => onDismiss(toast.id), SUCCESS_TOAST_MS);
    return () => clearTimeout(timer);
  }, [sticky, toast.id, onDismiss]);
  return <div className={cls("card", "toast", toast.kind === "failure" && "toast-failure")} data-toast={toast.kind}>
    <div className="toast-body">
      <p>{toast.message}</p>
      {toast.detail && <p className="small">{toast.detail}</p>}
      {toast.actions && <div className="actions">{toast.actions}</div>}
    </div>
    <button type="button" className="toast-dismiss" aria-label="Dismiss" onClick={() => onDismiss(toast.id)}><span aria-hidden="true">×</span></button>
  </div>;
}

/**
 * Write feedback (design system §4.5): bottom right at 24rem, full width above the bottom bar on phones. Two live
 * regions are always present, so each toast is announced once: success in the polite one, failure in the assertive
 * one. Activity keeps the record, so a success toast may leave after 5 s.
 */
export function ToastProvider({ children }: { children?: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const next = useRef(0);
  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((toast) => toast.id !== id)), []);
  const show = useCallback((toast: ToastOptions) => {
    next.current += 1;
    const id = next.current;
    setToasts((list) => [...list, { ...toast, kind: toast.kind ?? "success", id }]);
    return id;
  }, []);
  const api = useMemo(() => ({ show, dismiss }), [show, dismiss]);
  const stack = (kind: ToastEntry["kind"]) => toasts.filter((toast) => toast.kind === kind).map((toast) => <Toast key={toast.id} toast={toast} onDismiss={dismiss} />);
  return <ToastContext.Provider value={api}>
    {children}
    <div className="toasts">
      <div className="toast-stack" role="status">{stack("success")}</div>
      <div className="toast-stack" role="alert">{stack("failure")}</div>
    </div>
  </ToastContext.Provider>;
}

/** Shows and dismisses toasts; it needs a `ToastProvider` above it (#1124's root provides one). */
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast needs a ToastProvider above it");
  return api;
}
