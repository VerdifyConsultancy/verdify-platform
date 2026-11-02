// SPDX-License-Identifier: MIT
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { OperatorOnly, useWritesEnabled } from "../lib/session";
import { cls } from "./common";

export type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  /** A verb and its object: "Run wall drip", "Save". */
  children: ReactNode;
  variant?: "default" | "primary" | "stop";
  /** While the request is out: reads "Sending…" and is disabled. No spinner. */
  busy?: boolean;
  /** A control that changes something: carries `data-write-control` and renders only for operators. */
  write?: boolean;
  /** Full width on phones (Run, Stop all water, Acknowledge). */
  wide?: boolean;
};

/**
 * Weather's 44 px `.btn`. `variant="stop"` is `.btn-stop` and is never confirmed. A write control is disabled while
 * changes are switched off (`writes_enabled: false`), except Stop, which stays live (design system §4.5).
 */
export function Button({ children, variant = "default", busy = false, write = false, wide = false, disabled, className, type = "button", ...rest }: ButtonProps) {
  const writesEnabled = useWritesEnabled();
  const switchedOff = write && variant !== "stop" && !writesEnabled;
  const button = <button {...rest} type={type}
    className={cls("btn", variant === "primary" && "btn-primary", variant === "stop" && "btn-stop", wide && "btn-wide", className)}
    disabled={disabled || busy || switchedOff} data-write-control={write ? "true" : undefined}>
    {busy ? "Sending…" : children}
  </button>;
  return write ? <OperatorOnly>{button}</OperatorOnly> : button;
}
