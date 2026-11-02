// SPDX-License-Identifier: MIT
/**
 * The form controls, the one edit drawer and the water confirm dialog (design system §4.3 and §4.4). Screens wire them
 * to their rows through the write hooks in `app/lib/writes.ts`: Settings (#1132), Activity (#1133), Now (#1129) and
 * Water (#1135, #1136, #1137, #1182).
 */
export { ConfirmDialog, lineLabel, RUN_BOUNDS, RUN_DEFAULTS, runFields, type ConfirmDialogProps, type RunBody, type RunField } from "./confirm-dialog";
export { EditDrawer, GRAPH_UNITS, graphLink, type EditDrawerProps } from "./edit-drawer";
export { describedBy, FieldError, FieldHint, useFieldIds } from "./field";
export { MANUAL_DEFAULT, ManualToggle } from "./manual-toggle";
export { boundsHint, NumberField, savedText, stepValue, type NumberFieldProps } from "./number-field";
export { Reason, type ReasonProps } from "./reason";
export { Select, type SelectOption, type SelectProps } from "./select";
export { Switch, type SwitchProps } from "./switch";
export {
  OFFSET_MAX, OFFSET_MIN, resolvedStart, startDraft, startFromDraft, TimeField, WEEKDAYS, WeekdayChips, type ScheduleStart, type SolarDay, type StartDraft,
  type StartKind, type TimeFieldProps, type Weekday,
} from "./time-field";
