// SPDX-License-Identifier: MIT
// Generated from docs/design/dashboard/openapi/dashboard-api.v1.yaml; run pnpm contract:write.
export interface paths {
    readonly "/auth/callback": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Finish sign-in
         * @description Exchanges the code with the client secret and verifier, verifies the ID token (sub, preferred_username, name, groups), maps groups to a role (operators wins), creates the session with INT-01 and sets __Host-verdify_session (HttpOnly, Secure, SameSite=Lax, Path=/, 12 h). No tokens reach the browser. The login cookie is cleared on every outcome; a refusal answers its status with the SCR-09 card (text/html) at the same URL.
         */
        readonly get: operations["BFF-02"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/auth/login": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Start sign-in
         * @description Sets the sealed __Host-verdify_login cookie (state, nonce, PKCE verifier and returnTo; AES-256-GCM; 10 min) and redirects to the authorization_endpoint discovered from issuer https://auth.vallery.net/application/o/verdify-dashboard/ (https://auth.vallery.net/application/o/authorize/) with client_id=verdify-dashboard, scope openid profile email groups and code_challenge_method=S256. A page without a session answers 302 to /auth/login?returnTo=<path> (for /, returnTo=%2F); MFA happens at Authentik. A refusal answers its status with the SCR-09 card (text/html) at the same URL.
         */
        readonly get: operations["BFF-01"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/auth/logout": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Sign out
         * @description Needs the exact Origin. Deletes the session (INT-02), clears the cookie and redirects to Authentik's end-session endpoint with post_logout_redirect_uri https://dashboard.verdify.ai/. With Authentik's discovery unavailable it still deletes and clears, then redirects to /.
         */
        readonly post: operations["BFF-03"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/healthz": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Liveness, static 200 (successful probes are not logged) */
        readonly get: operations["OPS-01"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/internal/sessions": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Create a session after the BFF verified the ID token
         * @description Returns the token once; dashboard_session keeps only its sha256. X-Authentik-* headers are ignored. A request that carries X-Dashboard-Session is refused (403): the proxy always sends that header and the BFF's session-create call never does, so no proxied user request can reach this route.
         */
        readonly post: operations["INT-01"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/internal/sessions/current": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        readonly post?: never;
        /** Delete the session named by X-Dashboard-Session */
        readonly delete: operations["INT-02"];
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/readyz": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Readiness with the source revision */
        readonly get: operations["OPS-02"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/activity": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Every write by people, Iris, agents and the system, newest first */
        readonly get: operations["API-17"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/alerts": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Open alerts by severity then age, or recently resolved ones, repeats merged, each with its runbook */
        readonly get: operations["API-14"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/alerts/{id}/ack": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Acknowledge, with an optional note
         * @description Acknowledges every unacknowledged repeat of the alert's alert_type and sensor_id in one transaction, records the person and note on each and returns the merged row, so the open-alert count drops by one.
         */
        readonly post: operations["API-15"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/alerts/{id}/resolve": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Resolve, with an optional note (never blocked)
         * @description Resolves every unresolved repeat of the alert's alert_type and sensor_id in one transaction, records the person and resolution on each and returns the merged row. An alert whose condition persists re-opens on the next monitor cycle.
         */
        readonly post: operations["API-16"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/band": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Band in force and the reconstructed crop band, for the house or one zone
         * @description band is the history of the dispatcher's band rows for `zone` (setpoint_snapshot band_role): DERIVED from crop_band_anchors with the controller's formula, kept 90 days, null before that. crop is reconstructed from today's anchors over the whole window: for house, fn_band_setpoints(ts) over the step series, which gives the same crop values as fn_band_timeline; for another zone, the house temperature crop band with fn_zone_vpd_targets(ts) ∓ the zone crop's crop_band_anchors widths. The step matches the window: at least 5 min and at most 2,000 points.
         */
        readonly get: operations["API-06"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/band/preview": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Preview a crop-band edit as the day's diurnal curve, before Save
         * @description Read-only; Save stays API-10. Overlays the draft value on the crop_band_anchors rows fn_crop_band_value reads and returns the current and draft curves at 10-min steps with its four-anchor harmonic. A house temperature or VPD param returns that axis's low, target and high; a zone param returns the zone's VPD target and the target ∓ its widths. Codes: unknown_target (not a crop-band anchor or width), out_of_range (outside planner_effective_bounds).
         */
        readonly get: operations["API-19"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/equipment": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Equipment now, runtime and cycles today, the grow lights' Lutron state and controller intent, and why */
        readonly get: operations["API-13"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/equipment/lights/{light}/{state}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Turn a grow light on or off through verdify-setpoint-server (Lutron)
         * @description Calls verdify-setpoint-server POST /lights/{light}/{state} (12 s cap) and records one activity_log row: light.on or light.off, target grow_light_main or grow_light_grow, detail {light, state, result}. 200 when setpoint-server saw Lutron reach the state. When Home Assistant or Lutron does not confirm: 503 unavailable, retryable, with result ha_failed, not_confirmed or timeout; the light may still switch late. The board's state comes only from API-13's Lutron readback. Back to automatic is API-10 on sw_gl_main_auto_mode or sw_gl_grow_auto_mode. The writes flag gates it like every write, and it never touches the ESP32.
         */
        readonly post: operations["API-20"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/equipment/override": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Bursts and restart through manual_override (replaces the Home Assistant buttons)
         * @description fans, humidify and vent_bypass set only the controller's deadline latch for that burst to now + minutes; clear zeroes them; restart reboots the controller safely. It never changes occupancy. Now shows a burst from the controller's readback, not from this command. No MCP tool in MVP. Before the engine firmware: 503 unavailable.
         */
        readonly post: operations["API-IRR-12"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Lines, active runs, program sync and the engine flag, minutes today, limits, leak and occupancy
         * @description Polled every 15 s, and every 5 s while a program write is requested or a command is pending. Before the engine firmware, engine is false, program.src is null with its reason, and line states come from relay edges only.
         */
        readonly get: operations["API-IRR-01"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/clear-fault": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Clear a mister watchdog latch (irr_command clear_fault)
         * @description With line_id (L04, L07 or L09), clears that zone's D-IRP-19 latch; without it, every latch. There is no drip leak latch (Round 2 #3). Before the engine firmware: 503 unavailable.
         */
        readonly post: operations["API-IRR-13"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/lines/{line_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        /**
         * Enable or disable a line with its check; VPD and Schedule flags on misters; caps; pause or resume; manual
         * @description line_id all accepts only paused_until (pause or resume every line). vpd_member and schedule_member are for L04, L07 and L09. Enabling a line with no check, or turning schedule_member on for the first time, needs checked true, which only the dashboard sends (check_required). Disabling stops everything on the line, VPD misting included. Pause, resume and the check never change manual; any other edit that omits it stores true (R3). Codes: out_of_range, check_required, unknown_target, request_id_reused; Iris only, on a manual line, manual.
         */
        readonly put: operations["API-IRR-08"];
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/lines/{line_id}/run": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Run now
         * @description Starts at once or is refused: line_busy, manifold_busy, cap_reached, line_disabled, line_paused, line_faulted, not_schedule_member (mist). The API refuses what it can see in the newest irr_state (422); otherwise the line's irr_state shows whether it started. Before the engine firmware only L01 runs, through the btn_wall_clean bridge, for its configured irrig_wall_duration_min (minutes, if sent, must equal it); any other line is 503 unavailable and nothing is stored.
         */
        readonly post: operations["API-IRR-06"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/minutes": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Minutes, pulses and gallons per day by zone, line or cause (v_irrigation_minutes_daily), up to 400 days */
        readonly get: operations["API-IRR-10"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/runs": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Run ledger, newest first; windows up to 31 days */
        readonly get: operations["API-IRR-11"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/schedules": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Schedules with sync state, the 7-day week planned against actual, bounds per line kind and solar times */
        readonly get: operations["API-IRR-02"];
        readonly put?: never;
        /**
         * Create a schedule
         * @description Codes: out_of_range, too_close (30 min on one line), too_many_schedules (24), unknown_target, request_id_reused. A schedule on a disabled line, one over the line's daily cap, and a mist schedule on a zone with schedule_member off are all accepted; their occurrences end skipped or cap, with the reason in words.
         */
        readonly post: operations["API-IRR-03"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/schedules/{slot_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly slot_id: components["parameters"]["SlotId"];
            };
            readonly cookie?: never;
        };
        readonly get?: never;
        /**
         * Edit, enable or disable a schedule; set manual
         * @description Omitted fields keep their value, except manual: a REST edit that omits it stores true (R3). Same codes as API-IRR-03; Iris only, on a manual schedule, manual.
         */
        readonly put: operations["API-IRR-04"];
        readonly post?: never;
        /** Delete a schedule (its slot_id is never reused) */
        readonly delete: operations["API-IRR-05"];
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/irrigation/stop": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Stop one line, or every line when line_id is omitted
         * @description Never blocked, including by DASHBOARD_WRITES_ENABLED; never heap-deferred; sent ahead of any program push and re-sent until the relays read OFF (at most 2 min). Ends what is running now and disables nothing: VPD misting, survival fog and the next scheduled run can still start (disable the line to keep it off), and the dashboard's Stop result toast (VD-05) and the MCP tool description (VD-64, VD-61) say so. Before the engine firmware it works on one line or every line through the run-now bridge: it ends the legacy drip job (the sw_irrig_* enables) and turns the open wet relays off; their guards block only turn-on, so VPD misting may pulse again.
         */
        readonly post: operations["API-IRR-07"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/me": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Signed-in user, role, writes flag and API revision */
        readonly get: operations["API-01"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/metrics": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Metric registry over every numeric climate column, controller vitals and the daily scorecard keys
         * @description Column names never come from the browser. DLI keys return with the PAR meter (Round 2
         */
        readonly get: operations["API-03"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/now": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Now hero
         * @description Indoor and outdoor climate with ages, the band in force (the dispatcher's DERIVED band rows) with a verdict per metric, mode, Iris status, equipment running, bursts from the controller's readback, the leak flag, the open-alert count, source freshness with the ingestor heartbeat, and the device writer. Polled every 15 s; the shell banners read `freshness`, `writer`, `leak` and `alerts`. Water and occupancy come from API-IRR-01.
         */
        readonly get: operations["API-02"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/plan": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Plan in force, one lane per planned setting, manual settings and planner health
         * @description Each lane says whose value applies now (applied_from) and marks waypoints that will not apply (effective false), for example while a newer non-manual desired row holds until Iris's next plan. Waypoints carry their source: iris, or preemptive for the forecast action engine (a forecast rule, never shown as AI). health is planner_trigger_ledger: the rows expected in the last 36 h and the expected rows ahead.
         */
        readonly get: operations["API-07"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/series": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Downsampled series for uPlot
         * @description time_bucket_gapfill over [from, to). The bucket is the smallest of 1 min, 5 min, 15 min, 1 h, 3 h and 1 d that gives at most `points` buckets. Over 31 days, or when `from` is older than the raw retention of a requested metric that has a daily binding, the bucket is 1 d and every metric with a daily binding reads `daily_summary` (its `source` says so, and so does the chart); `daily.*` keys read `v_daily_scorecard`. Windows up to 400 days (else 422 `window_too_large`); buckets before a metric's `retained_from` are beyond its retention, not gaps. An unknown key is 422 `unknown_target`; the 10 s statement timeout is 503 `query_timeout`.
         */
        readonly get: operations["API-04"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/settings": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Every registry row with its device value, desired row, plan value and state, and the Band in force rows */
        readonly get: operations["API-09"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/settings/{param}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                /** @description tunable_registry name */
                readonly param: components["parameters"]["Param"];
            };
            readonly cookie?: never;
        };
        readonly get?: never;
        /**
         * Write a setting
         * @description Validates (known and writable, type and range, snap), then appends one desired_state row and one activity_log row in one transaction and wakes the dispatcher. Codes: unknown_target, read_only, out_of_range, request_id_reused. A crop_band anchor write also updates its crop_band_anchors rows; an irrigation_site setting is delivered in the irrigation program, not by the dispatcher (R12). Before the engine firmware an irrigation_site write is stored and reads requested with state_reason no_engine until a program compiled after it is confirmed.
         */
        readonly put: operations["API-10"];
        readonly post?: never;
        /**
         * Release to auto
         * @description Appends a value = null row. The plan, the band or the dispatcher's default applies again; a writable setting with none of these returns to its registry default (TunableDef.default). manual is cleared.
         */
        readonly delete: operations["API-11"];
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/settings/{param}/history": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Desired rows, plan values and readback steps over a window (also the Graphs setting steps) */
        readonly get: operations["API-12"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/states": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Equipment on/off intervals for the lanes
         * @description From equipment_state, seeded with the state at `from` (fn_equip_at). Wet lines carry their cause from the run ledger. Unknown stays unknown. Windows up to 31 days.
         */
        readonly get: operations["API-05"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/v1/water/daily": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Daily water use from the site meter by line and cause, with no-valve water apart
         * @description One entry per America/Denver day, in whole gallons: the meter's day total, Verdify-valve water by line and by cause, and the water that ran while no Verdify valve was open (the hose, sink, hydro or a leak). The parts are attributed per run and can sit a gallon off the total; nothing is rescaled. A no_data day is null, never 0. There is no rate and no flow flag.
         */
        readonly get: operations["API-21"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        readonly ActivityEntry: {
            /**
             * @example setting.write
             * @example setting.release
             * @example plan.write
             * @example alert.ack
             * @example irrigation.schedule.update
             */
            readonly action: string;
            readonly actor: components["schemas"]["Actor"];
            /** Format: date-time */
            readonly confirmed_at?: string | null;
            /** @description Before and after, e.g. {"before": 1.6, "value": 1.55, "requested": 1.53, "manual": {"before": false, "after": true}}; a plan lists its changes; trigger_id when an agent gave one */
            readonly detail?: Record<string, unknown>;
            /**
             * Format: uuid
             * @description Set when the write landed while an experiment was armed; nothing is refused
             */
            readonly experiment_id?: string | null;
            readonly id: number;
            readonly reason?: string | null;
            /**
             * Format: uuid
             * @description The Idempotency-Key or MCP request_id
             */
            readonly request_id?: string | null;
            /** @description Readback outcome of a setting or program write */
            readonly state?: components["schemas"]["ReadbackState"] | null;
            /** @description Param, plan id, alert id, line or slot id */
            readonly target: string;
            /** Format: date-time */
            readonly ts: string;
        };
        readonly ActivityPage: components["schemas"]["Page"] & {
            readonly items: readonly components["schemas"]["ActivityEntry"][];
        };
        /** @description Who wrote (R4). system is the platform itself, for example the forecast action engine */
        readonly Actor: {
            /** @description Authentik username, iris, the agent name (admin for the admin MCP token) or forecast-engine */
            readonly name: string;
            /**
             * @example dashboard
             * @example iris
             * @example agent:steward
             * @example agent:admin
             */
            readonly source: string;
        };
        /** @description One alert_log row, or one merged row for repeats of one alert_type and sensor_id */
        readonly Alert: {
            /** Format: date-time */
            readonly acknowledged_at?: string | null;
            /** @description Username, agent or Slack user */
            readonly acknowledged_by?: string | null;
            /**
             * @example leak_detected
             * @example irrigation_fence_breach
             * @example irrigation_run_missed
             * @example irrigation_line_faulted
             * @example setpoint_unconfirmed
             */
            readonly alert_type: string;
            /**
             * @example water
             * @example system
             */
            readonly category: string;
            /** @description Repeats of this alert_type and sensor_id merged into the row */
            readonly count?: number;
            /** @description As stored, e.g. line_id or equipment */
            readonly details?: Record<string, unknown>;
            /** Format: date-time */
            readonly first_seen_at: string;
            /** @description The oldest unresolved repeat's id, stable while new repeats merge (a resolved merged row: its oldest repeat's); API-15 and API-16 act on every repeat of its alert_type and sensor_id */
            readonly id: number;
            /**
             * Format: date-time
             * @description Newest of the merged repeats
             */
            readonly last_seen_at?: string | null;
            readonly message: string;
            readonly metric_value?: number | null;
            readonly notes?: string | null;
            readonly resolution?: string | null;
            /** Format: date-time */
            readonly resolved_at?: string | null;
            readonly resolved_by?: string | null;
            /** @description Markdown from slack_alert_runbooks */
            readonly runbook?: string | null;
            /** @enum {string} */
            readonly severity: "critical" | "warning" | "info";
            /**
             * @description disposition; a merged row is open while any of its repeats is unacknowledged
             * @enum {string}
             */
            readonly status: "open" | "acknowledged" | "resolved" | "false_positive" | "suppressed";
            readonly threshold_value?: number | null;
            readonly zone?: string | null;
        };
        readonly AlertPage: components["schemas"]["Page"] & {
            readonly items: readonly components["schemas"]["Alert"][];
        };
        readonly BandColumns: {
            readonly temp_high: components["schemas"]["NullableNumbers"];
            readonly temp_low: components["schemas"]["NullableNumbers"];
            readonly vpd_high: components["schemas"]["NullableNumbers"];
            readonly vpd_low: components["schemas"]["NullableNumbers"];
        };
        readonly BandCurve: {
            readonly high: components["schemas"]["NullableNumbers"];
            readonly low: components["schemas"]["NullableNumbers"];
            readonly target: components["schemas"]["NullableNumbers"];
        };
        readonly BandPosition: {
            readonly high: number | null;
            readonly low: number | null;
            /** @enum {string} */
            readonly verdict: "in" | "above" | "below" | "unavailable";
        };
        /** @description API-19: the band over one day as stored (current) and with the draft value (draft), one point per step_s */
        readonly BandPreview: {
            readonly current: components["schemas"]["BandCurve"];
            /**
             * Format: date
             * @description America/Denver day
             */
            readonly day: string;
            readonly draft: components["schemas"]["BandCurve"];
            readonly param: string;
            /** @constant */
            readonly step_s: 600;
            /** @description Epoch seconds */
            readonly t: readonly number[];
            /**
             * @example °F
             * @example kPa
             */
            readonly unit: string;
        };
        /** @description One point per step_s */
        readonly BandTimeline: {
            /** @description In force (DERIVED band rows of the requested zone); null beyond 90 days */
            readonly band: components["schemas"]["BandColumns"];
            /** @description Reconstructed from today's anchors (API-06 says how per zone) */
            readonly crop: components["schemas"]["BandColumns"];
            /** Format: date-time */
            readonly from: string;
            readonly step_s: number;
            /** @description Epoch seconds */
            readonly t: readonly number[];
            /** Format: date-time */
            readonly to: string;
        };
        /** @description An irrigation_command row: pending until the ingestor marks it sent, just before it calls the device (sent at most once), or failed when not sent within 2 min. Relay edges and irr_state show the outcome */
        readonly Command: {
            readonly actor: components["schemas"]["Actor"];
            /** @description The request body as stored */
            readonly args?: Record<string, unknown>;
            /** Format: uuid */
            readonly command_id: string;
            /** Format: date-time */
            readonly created_at: string;
            /** @enum {string} */
            readonly kind: "run" | "stop" | "clear_fault" | "override";
            /** @description Null means every line */
            readonly line_id: string | null;
            readonly reason?: string | null;
            /** Format: date-time */
            readonly sent_at?: string | null;
            /** @enum {string} */
            readonly status: "pending" | "sent" | "failed";
        };
        /** @description ESP32 diagnostics; a reset of uptime_s confirms a restart */
        readonly Controller: {
            readonly firmware_version?: string | null;
            /** @description kB, as stored in diagnostics.heap_bytes */
            readonly heap_free_kb?: number | null;
            readonly heap_largest_block_kb?: number | null;
            /** Format: date-time */
            readonly observed_at?: string | null;
            readonly reset_reason?: string | null;
            readonly rssi_dbm?: number | null;
            readonly uptime_s?: number | null;
        };
        /** @description The newest desired_state row for a setting and its readback state. value null means released. */
        readonly DesiredSetting: {
            readonly actor: components["schemas"]["Actor"];
            /** Format: date-time */
            readonly confirmed_at?: string | null;
            /** Format: date-time */
            readonly created_at: string;
            readonly id: number;
            /** @description Iris skips the setting while true */
            readonly manual: boolean;
            /** @description The flag before this write (write responses) */
            readonly manual_before?: boolean | null;
            readonly param: string;
            readonly reason?: string | null;
            /** @description Submitted value when snapping changed it (write responses) */
            readonly snapped_from?: number | null;
            readonly state: components["schemas"]["ReadbackState"];
            readonly state_reason?: components["schemas"]["StateReason"] | null;
            /** @description Stored value after snapping */
            readonly value: number | null;
        };
        readonly Equipment: {
            /** @description What holds fog (L11) off */
            readonly fog_block_reason?: components["schemas"]["ReasonState"];
            readonly items: readonly {
                /** @description From collapsed ON/OFF edges */
                readonly cycles_today?: number | null;
                /**
                 * @example fan1
                 * @example heat2
                 * @example mister_center
                 * @example grow_light_main
                 */
                readonly equipment: string;
                /** @enum {string} */
                readonly group: "climate" | "water" | "light";
                /**
                 * @description Grow lights only: the controller's intent (grow_light_main_intent, grow_light_grow_intent)
                 * @enum {string|null}
                 */
                readonly intent?: "on" | "off" | "unknown" | null;
                readonly label: string;
                readonly line_id?: string | null;
                /** Format: date-time */
                readonly observed_at: string | null;
                readonly runtime_today_s?: number | null;
                /**
                 * Format: date-time
                 * @description Start of the current run of equal states
                 */
                readonly since?: string | null;
                /**
                 * @description The newest row while the controller is online; unknown only while it is offline or there is no row. For the vent, on means open; for the grow lights, the Lutron readback
                 * @enum {string}
                 */
                readonly state: "on" | "off" | "unknown";
            }[];
            /** @description Which branch chose the climate mode */
            readonly mode_reason?: components["schemas"]["ReasonState"];
            /** @description What holds the misters off */
            readonly moisture_block_reason?: components["schemas"]["ReasonState"];
        };
        /** @description How old a source is. A sensor source is stale when its age exceeds twice its cadence; the ingestor heartbeat (cadence 60 s) after 180 s; a change-only source (equipment_state, irr_state) only while the controller is offline; a source never seen is stale */
        readonly Freshness: {
            readonly age_s: number | null;
            readonly cadence_s?: number;
            /** Format: date-time */
            readonly observed_at: string | null;
            readonly stale: boolean;
        };
        /** @enum {string} */
        readonly IrrigationCause: "vpd" | "schedule" | "run_now" | "feed" | "prewet" | "flush" | "fallback" | "unknown";
        readonly IrrigationLine: {
            /** @description The one-time physical check (D-IRP-07, D-IRP-08), recorded on the dashboard; null until someone records it */
            readonly check: null | {
                /** Format: date-time */
                readonly checked_at: string;
                readonly checked_by: string;
                readonly note?: string | null;
            };
            readonly clean_partner?: string | null;
            /** @description 0 means none */
            readonly daily_cap_min: number;
            readonly enabled: boolean;
            /**
             * @description D-IRP-19 watchdog: a back-off, or a latch until midnight or a clear
             * @enum {string|null}
             */
            readonly fault?: "backing_off" | "mister_fault" | null;
            /** Format: date-time */
            readonly fault_until?: string | null;
            /** @enum {string} */
            readonly kind: "drip" | "mister" | "fert_drip" | "fert_mister" | "master" | "fog";
            readonly last_outcome?: null | {
                /** Format: date-time */
                readonly at: string;
                readonly outcome: components["schemas"]["RunOutcome"];
                readonly reason?: string | null;
            };
            /** @description The newest run on the line that opened a valve: relay-edge runs, joined to their ledger row when there is one; the run in progress and VPD episodes included */
            readonly last_water?: null | {
                /** Format: date-time */
                readonly at: string;
                readonly cause: components["schemas"]["IrrigationCause"];
                readonly open_s: number;
                readonly pulses?: number | null;
            };
            readonly line_id: components["schemas"]["LineId"];
            /** @description Iris skips this line while true */
            readonly manual: boolean;
            /** @description The flag before this write (write responses) */
            readonly manual_before?: boolean | null;
            /** @example Drip • Wall */
            readonly name: string;
            readonly next_run?: components["schemas"]["NextRun"] | null;
            /** @description That row's reason; null when the line is not paused or none was given */
            readonly pause_reason?: string | null;
            /** @description Who set the current paused_until: the newest activity_log row with action irrigation.line.update, target the line or all, whose detail sets paused_until. Null when the line is not paused */
            readonly paused_by?: components["schemas"]["Actor"] | null;
            /** Format: date-time */
            readonly paused_until: string | null;
            /** @description True for L05 and L06 (Round 2b E): schedulable, no bring-up now; shown reserved while disabled */
            readonly reserved: boolean;
            /** @description Misters only; confirmed by the program hash */
            readonly schedule_member?: boolean | null;
            readonly state: components["schemas"]["LineState"];
            /** @description This line's fields against the program hash */
            readonly sync: components["schemas"]["ReadbackState"];
            readonly today?: {
                /** @description Whole gallons */
                readonly gal?: number | null;
                readonly minutes?: number | null;
                readonly pulses?: number | null;
            };
            /** @description Misters L04, L07 and L09 only; confirmed by the program hash */
            readonly vpd_member?: boolean | null;
            readonly vpd_reserve_min?: number | null;
            /**
             * @description Own probe, peer mean or house average (D-IRP-13)
             * @enum {string|null}
             */
            readonly vpd_source?: "own" | "peer" | "house" | null;
            /**
             * @description The line's place: supply for L03, house for L11. Minutes by zone use fog for L11 and leave L03 out
             * @enum {string}
             */
            readonly zone: "wall" | "center" | "south" | "west" | "supply" | "house";
        };
        readonly IrrigationStatus: {
            /** @description The run on each manifold */
            readonly active_runs: readonly {
                readonly cause: components["schemas"]["IrrigationCause"];
                /** Format: date-time */
                readonly ends_at?: string | null;
                readonly line_id: components["schemas"]["LineId"];
                /** @enum {string} */
                readonly manifold: "drip" | "mister";
                readonly pulses_done?: number | null;
                readonly pulses_total?: number | null;
                readonly slot_id?: number | null;
                /** Format: date-time */
                readonly started_at: string;
            }[];
            /** @description The live controller connection has the irrigation engine (irr_command); false before the engine firmware */
            readonly engine: boolean;
            /** @description degraded_only slots are running (D-IRP-12) */
            readonly fallback_active: boolean;
            /** @description irr_state (published on transitions) */
            readonly freshness: components["schemas"]["Freshness"];
            readonly leak: components["schemas"]["Leak"];
            /** @description Per-line duty and drip caps are on each line (today.minutes against daily_cap_min) */
            readonly limits: {
                readonly saf4_gal: components["schemas"]["UsedOfMax"];
                readonly soft_budget_gal: components["schemas"]["UsedOfMax"];
            };
            /** @description L01-L11 */
            readonly lines: readonly components["schemas"]["IrrigationLine"][];
            readonly occupancy: components["schemas"]["Occupancy"];
            readonly program: components["schemas"]["ProgramSync"];
            /** Format: date-time */
            readonly server_time: string;
            /** @description Valve-open minutes today by zone: wall, center, south and west, and fog for L11's humidification. L03's supply minutes are never in zone totals. Partial day */
            readonly today: {
                /** Format: date */
                readonly date: string;
                readonly zones: {
                    readonly [key: string]: number | null;
                };
            };
        };
        /** @description leak_detected: flow on the one site meter with every Verdify valve closed, so also the hose, sink or hydro. It raises the leak alert ("Water flowing with every Verdify valve closed") and nothing else: nothing latches, and the engine firmware forces nothing off (Round 2 #3, Q-05). Until that release the current firmware keeps its fog, mister and drip interlock. The flow banner shows while alert_id is set and reads "Flow stopped at HH:MM" once stopped_at is set, never an all clear */
        readonly Leak: {
            readonly active: boolean | null;
            /** @description The open leak_detected alert's merged-row id (Alert.id); null when none is open */
            readonly alert_id: number | null;
            /** @description Whole-gallon meter, so 0 or about 1 in practice */
            readonly flow_gpm?: number | null;
            /** @description Whole gallons */
            readonly gal_last_10min?: number | null;
            /** Format: date-time */
            readonly since?: string | null;
            /**
             * Format: date-time
             * @description When flow fell under 0.1 gal/min for two meter readings while that alert is open; null while flow continues
             */
            readonly stopped_at: string | null;
        };
        /** @description API-20: a grow-light switch that Lutron confirmed. A failure is a 503 Problem with its result */
        readonly LightCommand: {
            /** @description The activity_log row (light.on or light.off) */
            readonly activity_id: number;
            /** @enum {string} */
            readonly light: "main" | "grow";
            /**
             * @description setpoint-server saw Lutron reach the state
             * @enum {string}
             */
            readonly result: "confirmed";
            /** @enum {string} */
            readonly state: "on" | "off";
        };
        /** @description Stop or clear-fault target; omit line_id for every line */
        readonly LineCommand: {
            readonly line_id?: components["schemas"]["LineId"];
            readonly reason?: string;
        };
        /** @description L01-L11 */
        readonly LineId: string;
        readonly LineState: {
            readonly cause?: components["schemas"]["IrrigationCause"] | null;
            /** @enum {string} */
            readonly code: "idle" | "running" | "waiting" | "blocked" | "paused" | "disabled" | "unknown";
            /** Format: date-time */
            readonly ends_at?: string | null;
            /**
             * Format: date-time
             * @description irr_state or relay edge
             */
            readonly observed_at?: string | null;
            /** @description Waiting or blocked reason in words, e.g. someone inside (Frigate) */
            readonly reason?: string | null;
            readonly slot_id?: number | null;
        };
        readonly LineUpdate: {
            /** @description Optional; dashboard only */
            readonly check_note?: string;
            /** @description Dashboard only (MCP tools do not take it); records checked_by and checked_at */
            readonly checked?: boolean;
            readonly daily_cap_min?: number;
            readonly enabled?: boolean;
            /** @description Iris skips the line while true. A REST definition edit (enabled, vpd_member, schedule_member, daily_cap_min or vpd_reserve_min) that omits it stores true (the dashboard default, R3); a body with none of those (only paused_until, the check or a reason) never changes it; MCP definition edits default false */
            readonly manual?: boolean;
            /**
             * Format: date-time
             * @description Null or now resumes; VPD misting carries on
             */
            readonly paused_until?: string | null;
            readonly reason?: string;
            /** @description L04, L07 and L09 only; on for the first time needs the check */
            readonly schedule_member?: boolean;
            /** @description L04, L07 and L09 only */
            readonly vpd_member?: boolean;
            /** @description At most daily_cap_min */
            readonly vpd_reserve_min?: number;
        };
        readonly Me: {
            readonly display_name?: string | null;
            /** @enum {string} */
            readonly role: "operator" | "viewer";
            /** @description Operator API build commit */
            readonly source_revision: string;
            readonly username: string;
            /** @description Set by the BFF from DASHBOARD_WRITES_ENABLED (the operator API returns true) */
            readonly writes_enabled: boolean;
        };
        readonly MetricDef: {
            readonly cadence_s: number;
            /** @description Has a daily_summary binding, used over 31 days and beyond raw retention */
            readonly daily: boolean;
            /**
             * @example house climate
             * @example zones
             * @example water and soil
             * @example light
             * @example outdoor
             * @example targets
             * @example controller
             * @example daily
             */
            readonly group: string;
            /**
             * @example climate.temp_avg
             * @example climate.vpd_avg
             * @example climate.flow_gpm
             * @example diagnostics.heap_free_kb
             */
            readonly key: string;
            readonly label: string;
            readonly note?: string | null;
            /** @enum {string} */
            readonly provenance: "MEASURED" | "DERIVED" | "PLANNED" | "MANUAL" | "AI";
            /** @description Raw retention: climate 365, diagnostics 180, setpoint_snapshot 90 */
            readonly retention_days: number;
            readonly unit: string;
            readonly zone?: string | null;
        };
        /** @description A period with coverage no_data has no rows; draw a gap, never 0 */
        readonly MinutesReport: {
            /** Format: date */
            readonly from: string;
            /** @enum {string} */
            readonly group_by: "zone" | "line" | "cause";
            readonly periods: readonly {
                /**
                 * @description Today and outage days are partial
                 * @enum {string}
                 */
                readonly coverage: "complete" | "partial" | "no_data";
                /** Format: date */
                readonly start: string;
            }[];
            /** @description Always split by cause and chemistry so charts can stack */
            readonly rows: readonly {
                readonly cause: components["schemas"]["IrrigationCause"];
                /** @enum {string} */
                readonly chemistry: "clean" | "fert";
                /** @description Whole gallons */
                readonly gal?: number | null;
                /** @description Zone (wall, center, south, west, and fog for L11's humidification), line id or cause. L03's supply minutes appear only by line or cause, never in zone totals */
                readonly group: string;
                readonly minutes: number;
                /** Format: date */
                readonly period: string;
                readonly pulses?: number | null;
            }[];
            /** Format: date */
            readonly to: string;
        };
        readonly NextRun: {
            readonly label?: string | null;
            readonly line_id: components["schemas"]["LineId"];
            /** @description Planned valve-open minutes */
            readonly minutes?: number | null;
            readonly slot_id: number;
            /** Format: date-time */
            readonly start_at: string;
        };
        readonly Now: {
            /** @description The one open-alert count every surface shows (nav, Now, Activity, API-14, MCP alerts): the unacknowledged (disposition open) v_open_alerts rows with repeats of one alert_type and sensor_id counted once (a merged row counts while any of its repeats is unacknowledged), by severity. It equals the rows with status open that API-14 returns (verdify_services.alerts.open_count) */
            readonly alerts: {
                readonly critical: number;
                readonly info: number;
                readonly open: number;
                readonly warning: number;
            };
            /** @description The band in force now: the dispatcher's newest band rows for zone house (DERIVED); rh_pct is null when there is no RH band */
            readonly band: {
                /**
                 * Format: date-time
                 * @description ts of the newest house band rows, for the band's age; null with no rows
                 */
                readonly observed_at?: string | null;
                readonly rh_pct: components["schemas"]["BandPosition"] | null;
                readonly temp_f: components["schemas"]["BandPosition"];
                readonly vpd_kpa: components["schemas"]["BandPosition"];
            };
            readonly controller?: components["schemas"]["Controller"];
            /** @description ingestor is the heartbeat: one system_state row (ingestor_heartbeat) every 60 s from the ingestor's own loop, stale after 180 s. Heartbeat stale: the ingestor is not running, and the controller keeps running on its own. Heartbeat fresh and climate older than 300 s: the controller is not reporting */
            readonly freshness: {
                readonly climate: components["schemas"]["Freshness"];
                readonly diagnostics: components["schemas"]["Freshness"];
                readonly ingestor: components["schemas"]["Freshness"];
                readonly readback: components["schemas"]["Freshness"];
            };
            readonly indoor: {
                readonly rh_pct: components["schemas"]["Reading"];
                readonly temp_f: components["schemas"]["Reading"];
                readonly vpd_kpa: components["schemas"]["Reading"];
            };
            readonly iris: {
                /** @description Plan in force; null means band and defaults */
                readonly plan_id: string | null;
                /**
                 * @description Newest MIDNIGHT, SUNRISE or SUNSET trigger
                 * @enum {string}
                 */
                readonly required_trigger: "ok" | "late" | "missed" | "failed";
                readonly trigger?: string | null;
                /** Format: date-time */
                readonly valid_until?: string | null;
            };
            readonly leak: components["schemas"]["Leak"];
            readonly mode: {
                /**
                 * @example IDLE
                 * @example SEALED_MIST
                 * @example SAFETY_COOL
                 */
                readonly code: string;
                /** @description mode_reason */
                readonly reason?: string | null;
                /** Format: date-time */
                readonly since?: string | null;
                /**
                 * @example WATCH
                 * @example FOG
                 * @example S1
                 * @example S2
                 */
                readonly stage?: string | null;
            };
            readonly outdoor: {
                readonly rh_pct: components["schemas"]["Reading"];
                readonly temp_f: components["schemas"]["Reading"];
            };
            /** @description Running bursts, from the controller's readback (fan_burst_active, fog_burst_active, vent_bypass_active), never from the command alone */
            readonly overrides: readonly {
                /**
                 * Format: date-time
                 * @description ON edge plus the command's minutes; null for a push-button burst
                 */
                readonly ends_at: string | null;
                /** @description Why the controller holds it off, e.g. from fog_block_reason */
                readonly held_reason?: string | null;
                /** @enum {string} */
                readonly kind: "fans" | "humidify" | "vent_bypass";
                /**
                 * Format: date-time
                 * @description The readback's ON edge
                 */
                readonly since: string;
            }[];
            /** @description Equipment commanded on now */
            readonly running: readonly {
                readonly cause?: components["schemas"]["IrrigationCause"] | null;
                /**
                 * @example heat1
                 * @example mister_center
                 */
                readonly equipment: string;
                readonly line_id?: string | null;
                /** Format: date-time */
                readonly since: string | null;
            }[];
            /** Format: date-time */
            readonly server_time: string;
            /** @description Device writer health (Round 2 #1): the writer-health tile and the stuck-delivery banner. age_s is the setpoints pipeline age (v_data_pipeline_health source setpoints: the newest setpoint push or device-reported change). stuck is true while the setpoint_delivery_stuck alert is open. differing counts settings whose effective value has differed from the newest readback for more than 10 min, leaving out held and clamped ones */
            readonly writer: {
                readonly age_s: number | null;
                readonly differing: number;
                /** Format: date-time */
                readonly last_at?: string | null;
                /** Format: date-time */
                readonly oldest_since?: string | null;
                readonly stuck: boolean;
            };
        };
        readonly NullableNumbers: readonly (number | null)[];
        /** @description Frigate occupancy as the ingestor pushes it to the controller, from Home Assistant's binary_sensor.greenhouse_zone_person_occupancy polled every 300 s (the only occupancy input). occupied null means unknown, and unknown never holds mist (D-IRP-10). */
        readonly Occupancy: {
            /** @description occupancy_inhibit; false means the occupancy mist hold is off */
            readonly hold_enabled?: boolean | null;
            readonly occupied: boolean | null;
            /**
             * Format: date-time
             * @description Last person detection
             */
            readonly seen_at: string | null;
        };
        readonly Occurrence: {
            readonly line_id: components["schemas"]["LineId"];
            /** @description Set when done; unknown when no outcome reached the platform */
            readonly outcome?: components["schemas"]["RunOutcome"] | null;
            /** Format: date-time */
            readonly planned_start: string;
            /** @description Why skipped, blocked or missed, in words, e.g. line disabled. A pause reads 'line paused by <name>: <reason>', the reason left out when none was given. A planned one may carry a note: only while VPD sensing is degraded, ends at the daily cap */
            readonly reason?: string | null;
            readonly run_id?: string | null;
            readonly slot_id: number;
            /** @enum {string} */
            readonly status: "planned" | "skipped" | "blocked" | "running" | "done";
        };
        readonly OverrideRequest: {
            /** @enum {string} */
            readonly kind: "fans" | "humidify" | "vent_bypass" | "clear" | "restart";
            /** @description Needed for fans, humidify and vent_bypass */
            readonly minutes?: number;
            readonly reason?: string;
        };
        /** @description Cursor page; each list schema adds items */
        readonly Page: {
            /** @description Pass as cursor; null on the last page */
            readonly next_cursor: string | null;
        };
        readonly Plan: {
            /** @description planner_trigger_ledger: rows expected in the last 36 h and the expected rows ahead */
            readonly health: readonly components["schemas"]["PlannerTrigger"][];
            readonly lanes: readonly components["schemas"]["PlanLane"][];
            readonly manual_count: number;
            /**
             * Format: date-time
             * @description With no plan, band and defaults apply since then
             */
            readonly no_plan_since?: string | null;
            /** @description Null when no plan is in force */
            readonly plan: components["schemas"]["PlanRecord"] | null;
        };
        readonly PlanLane: {
            /**
             * @description Computed by verdify_services with the dispatcher's rule
             * @enum {string}
             */
            readonly applied_from: "manual" | "desired" | "plan" | "band" | "default";
            /** @description Newest desired row; Iris skips it if manual */
            readonly desired: components["schemas"]["DesiredSetting"] | null;
            /** @description Newest readback */
            readonly device: components["schemas"]["Reading"];
            readonly label: string;
            readonly param: string;
            readonly unit?: string | null;
            /** @description Plan values to the horizon */
            readonly waypoints: readonly components["schemas"]["PlanWaypoint"][];
        };
        /** @description One planner_trigger_ledger row */
        readonly PlannerTrigger: {
            /**
             * Format: date-time
             * @description expected_at plus the SLA
             */
            readonly due_at: string;
            /**
             * Format: date-time
             * @description When the trigger fires
             */
            readonly expected_at: string;
            readonly failure_class?: string | null;
            /**
             * @description The ledger status verbatim; the UI maps it to words (missed and wrong_action included)
             * @enum {string}
             */
            readonly outcome: "expected" | "delivered" | "acked" | "plan_written" | "action_completed" | "neutral_fallback" | "wrong_action" | "delivery_failed" | "timed_out" | "missed";
            /** @description MIDNIGHT, SUNRISE and SUNSET are required */
            readonly required: boolean;
            /** Format: date-time */
            readonly resolved_at?: string | null;
            readonly trigger: string;
        };
        /** @description A plan_journal row; history rows omit detail */
        readonly PlanRecord: {
            readonly conditions_summary?: string | null;
            /** @description hypothesis_structured and climate_intents as stored (stress windows, per-setting rationale) */
            readonly detail?: Record<string, unknown>;
            readonly expected_outcome?: string | null;
            readonly experiment?: string | null;
            readonly hypothesis?: string | null;
            readonly lesson?: string | null;
            readonly outcome_score?: number | null;
            /** @example iris-20261101-0702 */
            readonly plan_id: string;
            /** @description lifecycle_status */
            readonly status?: string | null;
            /** @description MIDNIGHT, SUNRISE, SOLAR_MAX, TRANSITION, SUNSET, WEEKLY, FORECAST_DEVIATION or MANUAL */
            readonly trigger: string;
            /** Format: date-time */
            readonly valid_from?: string | null;
            /** Format: date-time */
            readonly valid_until?: string | null;
            /** Format: date-time */
            readonly written_at: string;
        };
        readonly PlanWaypoint: {
            /** Format: date-time */
            readonly at: string;
            /** @description false when a manual or newer desired row keeps it from applying (SUPERSEDED) */
            readonly effective: boolean;
            /**
             * @description preemptive is the forecast action engine, shown as "Forecast rule · system", never AI
             * @enum {string}
             */
            readonly source: "iris" | "preemptive";
            readonly value: number | null;
        };
        /** @description RFC 9457 problem details. The UI maps code to words and shows an unknown code raw. */
        readonly Problem: {
            readonly code: components["schemas"]["ProblemCode"];
            /** @description Current stored or device value */
            readonly current?: number | string | boolean | null;
            /** @description In plain words */
            readonly detail?: string;
            /** @description Request field at fault, e.g. on_s */
            readonly field?: string;
            readonly max?: number | null;
            readonly min?: number | null;
            /** @description The request id (the Idempotency-Key on a write), as logged */
            readonly request_id?: string;
            /**
             * @description API-20 only, with unavailable: Home Assistant failed the call, Lutron did not reach the state, or no answer within 12 s
             * @enum {string}
             */
            readonly result?: "ha_failed" | "not_confirmed" | "timeout";
            /** @description When a retry may succeed (line_busy, manifold_busy), if known */
            readonly retry_after_s?: number | null;
            readonly retryable: boolean;
            readonly status: number;
            readonly title: string;
            /**
             * Format: uri-reference
             * @default about:blank
             */
            readonly type?: string;
        };
        /**
         * @description Stable codes shared with MCP (architecture §5.3, irrigation §4.3 and §4.7). manual is for Iris only
         * @enum {string}
         */
        readonly ProblemCode: "invalid_request" | "unauthenticated" | "forbidden" | "origin_mismatch" | "not_found" | "writes_disabled" | "query_timeout" | "unavailable" | "window_too_large" | "unknown_target" | "read_only" | "out_of_range" | "manual" | "request_id_reused" | "check_required" | "too_close" | "too_many_schedules" | "line_disabled" | "line_paused" | "line_faulted" | "not_schedule_member" | "line_busy" | "manifold_busy" | "cap_reached";
        /** @description The compiled program against the controller (irr_program_id); confirmed when the device hash matches */
        readonly ProgramSync: {
            /** @description First 16 hex of the applied sha256 */
            readonly applied_hash?: string | null;
            readonly applied_revision?: number | null;
            /** Format: date-time */
            readonly confirmed_at?: string | null;
            /** @description compile_program() over the tables now */
            readonly desired_sha256: string;
            /**
             * Format: date-time
             * @description Retries after 1, 5 and 15 min
             */
            readonly next_retry_at?: string | null;
            /** Format: date-time */
            readonly pushed_at?: string | null;
            /** @description Why there is no program on the controller, e.g. engine not on controller */
            readonly reason?: string | null;
            readonly reject_code?: string | null;
            /** @description Newest pushed revision */
            readonly revision?: number | null;
            /**
             * @description null before the engine firmware; rom = the built-in program
             * @enum {string|null}
             */
            readonly src: "nvs" | "rom" | null;
            readonly state: components["schemas"]["ReadbackState"];
        };
        /**
         * @description Computed on every read (architecture §5.5); display only. held means the platform applies something else on purpose (a dispatcher clamp, or an armed experiment manifest serving the field). Irrigation items and the irrigation_site settings use requested, confirmed and not_confirmed against the controller's program hash; before the engine firmware they stay requested.
         * @enum {string}
         */
        readonly ReadbackState: "requested" | "confirmed" | "not_confirmed" | "held" | "no_readback" | "overridden" | "released";
        readonly Readiness: {
            /**
             * @description BFF only; reported, not gating
             * @enum {string}
             */
            readonly authentik?: "reachable" | "unreachable";
            /**
             * @description BFF only; reported, not gating
             * @enum {string}
             */
            readonly operator_api?: "reachable" | "unreachable";
            readonly source_revision: string;
            /** @enum {string} */
            readonly status: "ready" | "not_ready";
        };
        readonly Reading: {
            /** Format: date-time */
            readonly observed_at: string | null;
            /** @description Why value is null, in words */
            readonly reason?: string | null;
            readonly value: number | null;
        };
        /** @description The newest system_state row of one controller reason entity. The UI words the code and shows an unknown one raw */
        readonly ReasonState: {
            /** @description Null while the controller is not reporting, or with no row */
            readonly code: string | null;
            /**
             * Format: date-time
             * @description The newest row's ts, kept while code is null
             */
            readonly observed_at: string | null;
            /**
             * Format: date-time
             * @description When the current value began, from the collapsed values; null while code is null
             */
            readonly since: string | null;
        };
        /** @description One run: a drip run, a fertilizer stage or a mist episode, from relay edges joined to its irrigation_ledger row. Listed once it has ended; the run in progress is in API-IRR-01 */
        readonly Run: {
            /** @description Null when the controller started it */
            readonly actor?: components["schemas"]["Actor"] | null;
            readonly cause: components["schemas"]["IrrigationCause"];
            /** @enum {string} */
            readonly chemistry: "clean" | "fert";
            /**
             * Format: uuid
             * @description The run-now command that started it
             */
            readonly command_id?: string | null;
            /** Format: date-time */
            readonly ended_at?: string | null;
            /** @description Whole gallons from the one site meter (it also counts the hose, sink and hydro); informational. 0 when the run moved no water; null without meter data */
            readonly gal?: number | null;
            readonly line_id: components["schemas"]["LineId"];
            /** @description 0 for skipped, missed and cap occurrences */
            readonly open_s: number;
            /** @description From the ledger; unknown when its outcome never reached the platform. A run with no ledger row (a VPD episode, or a run before the engine firmware) reads ok */
            readonly outcome: components["schemas"]["RunOutcome"];
            readonly pulses?: number | null;
            readonly reason?: string | null;
            readonly run_id: string;
            readonly schedule_label?: string | null;
            readonly slot_id?: number | null;
            /** Format: date-time */
            readonly started_at: string;
            readonly zone: string;
        };
        /**
         * @description unknown: no outcome reached the platform (it aged out of irr_events while the ingestor was disconnected); a gap, never missed, and it raises no alert. The controller never sends it
         * @enum {string}
         */
        readonly RunOutcome: "ok" | "short" | "stopped" | "preempted" | "skipped" | "missed" | "cap" | "unknown";
        readonly RunPage: components["schemas"]["Page"] & {
            readonly items: readonly components["schemas"]["Run"][];
        };
        /** @description Drips: minutes. Fertilizer drips (L02, L06): minutes, prewet_s, flush_s. Misters (schedule_member on): on_s, count, gap_s. Fertilizer misters (L08, L10): on_s, count and gap_s within the mister bounds, plus prewet_s and flush_s. Before the engine firmware only L01 runs, for its configured irrig_wall_duration_min */
        readonly RunRequest: {
            readonly count?: number;
            readonly flush_s?: number;
            readonly gap_s?: number;
            readonly minutes?: number;
            readonly on_s?: number;
            readonly prewet_s?: number;
            readonly reason?: string;
        };
        readonly Schedule: components["schemas"]["ScheduleInput"] & {
            /** @description The flag before this write (write responses) */
            readonly manual_before?: boolean | null;
            readonly slot_id: number;
            readonly sync: components["schemas"]["ReadbackState"];
            /** Format: date-time */
            readonly updated_at: string;
            readonly updated_by: components["schemas"]["Actor"];
        };
        readonly ScheduleAgenda: {
            /** @description Per line kind (drip, mister, fert_drip, fert_mister), from verdify_schemas/irrigation.py, so editors and agents see the limits */
            readonly bounds: {
                readonly [key: string]: {
                    readonly [key: string]: {
                        readonly max: number;
                        readonly min: number;
                    };
                };
            };
            readonly counts: {
                /** @constant */
                readonly max_schedules: 24;
                readonly schedules: number;
            };
            /** @description By planned start; the week view draws them per line and day */
            readonly occurrences: readonly components["schemas"]["Occurrence"][];
            readonly schedules: readonly components["schemas"]["Schedule"][];
            /** @description Per local day in the window, from verdify_schemas/solar.py, for previews */
            readonly solar: readonly {
                /** Format: date */
                readonly date: string;
                /** Format: date-time */
                readonly solar_noon: string;
                /** Format: date-time */
                readonly sunrise: string;
                /** Format: date-time */
                readonly sunset: string;
            }[];
        };
        readonly ScheduleCreate: components["schemas"]["ScheduleInput"] & {
            /**
             * @description Omitted on a REST create means true (the dashboard default); MCP creates default false
             * @default true
             */
            readonly manual?: boolean;
        };
        /** @description The weekdays the schedule runs on */
        readonly ScheduleDays: readonly ("mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun")[];
        /** @description Bounds per irrigation §4.3, checked per line kind on submit; values snap to whole seconds and minutes */
        readonly ScheduleInput: {
            /**
             * @description Pulses in a mist window; drips use 1
             * @default 1
             */
            readonly count?: number;
            readonly days?: components["schemas"]["ScheduleDays"];
            /**
             * @description L04, L07, L09 only (D-IRP-12)
             * @default false
             */
            readonly degraded_only?: boolean;
            /** @default true */
            readonly enabled?: boolean;
            /**
             * @description Fertilizer lines only
             * @default 0
             */
            readonly flush_s?: number;
            /**
             * @description Mister gap 30-600; drips 0
             * @default 0
             */
            readonly gap_s?: number;
            readonly label?: string;
            /** @description L01, L02, L04-L10 (not L03 or L11) */
            readonly line_id?: string;
            /**
             * @description Iris skips the schedule while true. A REST create or edit that omits it stores true (the dashboard default, R3); MCP writes default false
             * @default true
             */
            readonly manual?: boolean;
            /** @description Seconds per run or pulse; misters 10-90; fertilizer the feed */
            readonly on_s?: number;
            /**
             * @description Fertilizer lines only
             * @default 0
             */
            readonly prewet_s?: number;
            readonly reason?: string;
            readonly start?: components["schemas"]["ScheduleStart"];
        };
        readonly ScheduleStart: {
            /** @description Local HH:MM when kind is clock */
            readonly clock?: string;
            /** @enum {string} */
            readonly kind: "clock" | "sunrise" | "solar_noon" | "sunset";
            /**
             * @description Solar kinds
             * @default 0
             */
            readonly offset_min?: number;
        };
        /** @description uPlot columns. A bucket with no data is null, never 0; count gives coverage. */
        readonly Series: {
            /** @enum {integer} */
            readonly bucket_s: 60 | 300 | 900 | 3600 | 10800 | 86400;
            /** Format: date-time */
            readonly from: string;
            /** @description The data_gaps rows (telemetry outages the ingestor recorded, kept with no retention) that overlap [from, to), oldest first. A null run inside one is that outage; a null run with none has no known reason */
            readonly gaps?: readonly {
                /** Format: date-time */
                readonly end: string;
                /** @description As stored, e.g. ingestor_restart; the UI words it and shows an unknown one raw */
                readonly reason: string | null;
                /** Format: date-time */
                readonly start: string;
            }[];
            /** @description By metric key */
            readonly series: {
                readonly [key: string]: components["schemas"]["SeriesColumns"];
            };
            /** Format: date-time */
            readonly server_time: string;
            /** @description Bucket starts, epoch seconds */
            readonly t: readonly number[];
            /** Format: date-time */
            readonly to: string;
            /**
             * Format: date-time
             * @description Newest sample included
             */
            readonly watermark: string | null;
        };
        readonly SeriesColumns: {
            readonly avg: components["schemas"]["NullableNumbers"];
            readonly count: readonly number[];
            /**
             * Format: date-time
             * @description forecast.temp_f, forecast.rh_pct and forecast.vpd_kpa only: the newest weather_forecast.fetched_at used; null when the window holds no forecast row
             */
            readonly issued_at?: string | null;
            readonly max: components["schemas"]["NullableNumbers"];
            readonly min: components["schemas"]["NullableNumbers"];
            /** @description daily.* keys only, one per bucket: why a null bucket is null ('band grade kept 3 days', the water day's v_water_meter_daily quality, or 'no meter data'); null where the bucket has a value or no reason is known */
            readonly reason?: readonly (string | null)[];
            /**
             * Format: date-time
             * @description Older buckets are beyond retention_days, not gaps
             */
            readonly retained_from?: string | null;
            /**
             * @description daily_summary: daily summaries, and the chart says so; forecast: weather_forecast, the newest issue per hour
             * @enum {string}
             */
            readonly source: "raw" | "daily_summary" | "scorecard" | "forecast";
        };
        readonly SessionCreate: {
            /** @description name claim */
            readonly display_name?: string | null;
            /** @enum {string} */
            readonly role: "operator" | "viewer";
            /** @description preferred_username */
            readonly username: string;
        };
        readonly SessionToken: {
            /** Format: date-time */
            readonly expires_at: string;
            /** @description 32 random bytes, base64url; becomes __Host-verdify_session */
            readonly token: string;
        };
        /** @description One tunable_registry row, or a read-only Band in force row, as the Settings screen shows it */
        readonly Setting: {
            /**
             * @description What the dispatcher applies now
             * @enum {string}
             */
            readonly applied_from: "manual" | "desired" | "plan" | "band" | "default";
            /** @enum {string} */
            readonly control_class: "planner_policy" | "scheduled_policy" | "controller_safety" | "crop_band" | "irrigation_site" | "readback_context" | "retired";
            readonly desired: components["schemas"]["DesiredSetting"] | null;
            /**
             * @description band_in_force is the "Band in force" group: the dispatcher's DERIVED band rows (band_<zone>_<role>), house and per zone, read-only
             * @enum {string}
             */
            readonly group: "planner_policy" | "scheduled_policy" | "controller_safety" | "crop_band" | "site_water_caps" | "read_only" | "band_in_force";
            /** @enum {string} */
            readonly kind: "numeric" | "switch" | "enum";
            /** @description For a registry row, TunableDef.label (VD-29), filled from the setting's entity name in firmware/greenhouse/tunables.yaml and drift-tested */
            readonly label: string;
            readonly max?: number | null;
            /** @description planner_effective_bounds (registry intersected with the firmware clamp) */
            readonly min?: number | null;
            /** @description Enum settings only */
            readonly options?: readonly {
                readonly label: string;
                readonly value: number;
            }[];
            /** @example mister_engage_kpa */
            readonly param: string;
            readonly plan: null | {
                /** @description Next planned change; effective false while a newer desired row holds it off */
                readonly next: components["schemas"]["PlanWaypoint"] | null;
                readonly plan_id: string;
                /**
                 * @description preemptive is the forecast action engine
                 * @enum {string}
                 */
                readonly source: "iris" | "preemptive";
                /** @description Plan value in force now, as stored (unsnapped) */
                readonly value: number | null;
                /** Format: date-time */
                readonly written_at: string;
            };
            /** @example legacy wide limit; not the band while the controller runs its on-chip curve */
            readonly read_only_reason?: string | null;
            /** @description Newest readback; for irrigation_site rows the value in the applied program */
            readonly readback: components["schemas"]["Reading"] | null;
            /**
             * @description cfg_* sensor, a native number, switch or equipment_state mirror, the irrigation program hash (irrigation_site), or none
             * @enum {string}
             */
            readonly readback_via: "cfg" | "entity" | "program" | "none";
            /** @description Of the newest write, else of the applied value against the readback */
            readonly state: components["schemas"]["ReadbackState"];
            /** @description The registry step (from tunables.yaml; equal to ENTITY_GRIDS for the 48 policy fields) */
            readonly step?: number | null;
            /** @description For a registry row, TunableDef.unit (VD-29), filled from the same firmware entity and drift-tested; null when the setting has no unit */
            readonly unit?: string | null;
            /** @description false with read_only_reason; the irrigation_site rows are writable before the engine firmware too (their writes wait, state_reason no_engine) */
            readonly writable: boolean;
        };
        readonly SettingHistory: {
            /** @description Oldest first, from the row in force at from */
            readonly desired: readonly components["schemas"]["DesiredSetting"][];
            /** Format: date-time */
            readonly from: string;
            readonly max?: number | null;
            readonly min?: number | null;
            readonly param: string;
            /** @description Iris and forecast-rule values in the window, future waypoints included, each with its source and effective, so API-12 alone draws the Graphs setting steps */
            readonly plan: readonly components["schemas"]["PlanWaypoint"][];
            /** @description cfg_* readback change points */
            readonly readback: readonly components["schemas"]["TimedValue"][];
            /** Format: date-time */
            readonly to: string;
            readonly unit?: string | null;
        };
        readonly SettingWrite: {
            /**
             * @description Iris skips the setting while true. The dashboard default (R3); MCP set_setting defaults false. A write stores exactly the flag it carries
             * @default true
             */
            readonly manual?: boolean;
            readonly reason?: string;
            /** @description Switches 0 or 1, enums the option value; snapped to step */
            readonly value: number;
        };
        readonly StateLanes: {
            /** Format: date-time */
            readonly from: string;
            readonly lanes: readonly {
                /**
                 * @example fan1
                 * @example mister_center
                 * @example leak_detected
                 */
                readonly equipment: string;
                /** @enum {string} */
                readonly group: "climate" | "water" | "light" | "flag";
                readonly intervals: readonly {
                    readonly cause?: components["schemas"]["IrrigationCause"] | null;
                    /**
                     * Format: date-time
                     * @description Clamped to the window or now
                     */
                    readonly end: string;
                    /** Format: date-time */
                    readonly start: string;
                    /** @enum {string} */
                    readonly state: "on" | "off" | "unknown";
                }[];
                readonly label?: string;
                readonly line_id?: string | null;
            }[];
            /** Format: date-time */
            readonly to: string;
        };
        /** @description Why a value is held or not confirmed, when the platform knows (architecture §5.5): clamped (a dispatcher clamp; applied_value), experiment (an armed manifest serves the field), deferred (controller heap low), push_failed (setpoint_changes.delivery_status after the request), writer_down (the ingestor heartbeat is older than 180 s: "Ingestor not running since HH:MM"), controller_not_reporting (heartbeat fresh, newest climate row older than 300 s: "Controller not reporting since HH:MM"), no_engine (an irrigation_site write stored before the engine firmware: "applies with the irrigation firmware"; it stays requested) or no_match ("Sent HH:MM; no matching readback yet"). since gives each HH:MM */
        readonly StateReason: {
            /** @description What the dispatcher sent instead, when clamped */
            readonly applied_value?: number | null;
            /** @enum {string} */
            readonly code: "clamped" | "experiment" | "deferred" | "push_failed" | "writer_down" | "controller_not_reporting" | "no_engine" | "no_match";
            /** @description In words, e.g. the guard that clamped it or the push status */
            readonly detail?: string | null;
            /**
             * Format: date-time
             * @description When the reason began, null when not known. writer_down: the newest ingestor heartbeat. controller_not_reporting: the newest climate row. no_match, push_failed and deferred: the push the request went out in (the "Sent HH:MM" time). clamped: the clamp row. no_engine: the stored write
             */
            readonly since?: string | null;
        };
        readonly TimedValue: {
            /** Format: date-time */
            readonly at: string;
            readonly value: number | null;
        };
        readonly UsedOfMax: {
            readonly max: number | null;
            readonly used: number | null;
        };
        /** @description API-21: daily water use in whole gallons. There is no rate and no flow flag */
        readonly WaterUse: {
            /** @description One per America/Denver day, oldest first. On a no_data day every value is null, never 0 */
            readonly days: readonly {
                /** @description Verdify-valve gallons by IrrigationCause */
                readonly by_cause: {
                    readonly [key: string]: number;
                } | null;
                /** @description Verdify-valve gallons by line id */
                readonly by_line: {
                    readonly [key: string]: number;
                } | null;
                /**
                 * @description From v_water_meter_daily quality: ok is complete; unavailable, or no row, is no_data; anything else is partial
                 * @enum {string}
                 */
                readonly coverage: "complete" | "partial" | "no_data";
                /** Format: date */
                readonly date: string;
                /** @description The site meter's day total (main_pulse) */
                readonly meter_gal: number | null;
                /** @description Meter water while no Verdify valve was open: the hose, sink, hydro or a leak */
                readonly no_valve_gal: number | null;
            }[];
            /** Format: date */
            readonly from: string;
            /**
             * Format: date
             * @description Inclusive local day: today, a partial day
             */
            readonly to: string;
        };
    };
    responses: {
        /** @description Stored for the device writer: pending until sent (at most once), or failed if not sent within 2 min. Relay edges and the line's irr_state show what happened. */
        readonly Accepted: {
            headers: {
                readonly [name: string]: unknown;
            };
            content: {
                readonly "application/json": components["schemas"]["Command"];
            };
        };
        /** @description Validation or refusal; code says why and nothing was stored or sent */
        readonly Invalid: {
            headers: {
                readonly [name: string]: unknown;
            };
            content: {
                /**
                 * @example {
                 *       "title": "Out of range",
                 *       "status": 422,
                 *       "code": "out_of_range",
                 *       "detail": "Must be 0.50-2.50 kPa",
                 *       "retryable": false,
                 *       "field": "value",
                 *       "min": 0.5,
                 *       "max": 2.5,
                 *       "current": 1.6
                 *     }
                 */
                readonly "application/problem+json": components["schemas"]["Problem"];
            };
        };
        /** @description Error; see the Errors rule in the API description */
        readonly Problem: {
            headers: {
                readonly [name: string]: unknown;
            };
            content: {
                readonly "application/problem+json": components["schemas"]["Problem"];
            };
        };
    };
    parameters: {
        readonly AlertId: number;
        /** @description next_cursor from the previous page */
        readonly Cursor: string;
        /** @description Window start (inclusive) */
        readonly From: string;
        /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
        readonly IdempotencyKey: string;
        readonly Limit: number;
        readonly LineId: components["schemas"]["LineId"];
        /** @description tunable_registry name */
        readonly Param: string;
        readonly Reason: string;
        readonly SlotId: number;
        /** @description Window end (exclusive) */
        readonly To: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    readonly "BFF-02": {
        readonly parameters: {
            readonly query?: {
                readonly code?: string;
                readonly error?: string;
                readonly state?: string;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description To returnTo, with the session cookie set and the login cookie cleared */
            readonly 302: {
                headers: {
                    readonly Location?: string;
                    readonly "Set-Cookie"?: string;
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Sign-in did not complete (state mismatch, a missing, expired or tampered login cookie, an Authentik error or a refused code exchange); no session cookie */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "text/html": string;
                };
            };
            /** @description No access: signed in, but in neither verdify-dashboard-operators nor verdify-dashboard-viewers; no INT-01 call */
            readonly 403: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "text/html": string;
                };
            };
            /** @description Sign-in is unavailable: Authentik (discovery or the token endpoint) cannot be reached, or INT-01 fails; no session cookie */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "text/html": string;
                };
            };
        };
    };
    readonly "BFF-01": {
        readonly parameters: {
            readonly query?: {
                /** @description Same-origin relative path; anything else becomes / */
                readonly returnTo?: string;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description To Authentik */
            readonly 302: {
                headers: {
                    readonly Location?: string;
                    readonly "Set-Cookie"?: string;
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Sign-in is unavailable: Authentik cannot be reached (existing sessions keep working) */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "text/html": string;
                };
            };
        };
    };
    readonly "BFF-03": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description To the Authentik end-session endpoint, or to / while its discovery is unavailable */
            readonly 303: {
                headers: {
                    readonly Location?: string;
                    readonly "Set-Cookie"?: string;
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            readonly 403: components["responses"]["Problem"];
        };
    };
    readonly "OPS-01": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": {
                        /** @constant */
                        readonly status?: "ok";
                    };
                };
            };
        };
    };
    readonly "INT-01": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["SessionCreate"];
            };
        };
        readonly responses: {
            /** @description Created */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["SessionToken"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "INT-02": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Deleted */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "OPS-02": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Ready */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Readiness"];
                };
            };
            /** @description Not ready */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Readiness"];
                };
            };
        };
    };
    readonly "API-17": {
        readonly parameters: {
            readonly query?: {
                /** @description Prefix match, e.g. irrigation. or setting.write */
                readonly action?: string;
                readonly actor?: string;
                /** @description next_cursor from the previous page */
                readonly cursor?: components["parameters"]["Cursor"];
                readonly from?: string;
                readonly limit?: components["parameters"]["Limit"];
                /** @description dashboard, iris, system, agent (any agent) or agent:<name> */
                readonly source?: string;
                /** @description Exact target, e.g. L01 or mister_engage_kpa */
                readonly target?: string;
                readonly to?: string;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ActivityPage"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-14": {
        readonly parameters: {
            readonly query?: {
                /** @description next_cursor from the previous page */
                readonly cursor?: components["parameters"]["Cursor"];
                readonly limit?: components["parameters"]["Limit"];
                /** @description open (default) = every v_open_alerts row, acknowledged ones included. recent = open plus those resolved in the last 24 h. Repeats of one alert_type and sensor_id come back as one row with count and first and last seen; a merged row has status open while any of its repeats is unacknowledged. The rows with status open are the one open-alert count (API-02 alerts.open) */
                readonly status?: "open" | "recent";
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["AlertPage"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-15": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly id: components["parameters"]["AlertId"];
            };
            readonly cookie?: never;
        };
        readonly requestBody?: {
            readonly content: {
                readonly "application/json": {
                    readonly note?: string;
                };
            };
        };
        readonly responses: {
            /** @description The acknowledged alert */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Alert"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-16": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly id: components["parameters"]["AlertId"];
            };
            readonly cookie?: never;
        };
        readonly requestBody?: {
            readonly content: {
                readonly "application/json": {
                    readonly resolution?: string;
                };
            };
        };
        readonly responses: {
            /** @description The resolved alert */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Alert"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-06": {
        readonly parameters: {
            readonly query: {
                /** @description Window start (inclusive) */
                readonly from: components["parameters"]["From"];
                /** @description Window end (exclusive) */
                readonly to: components["parameters"]["To"];
                /** @description North has no zone band and is not offered */
                readonly zone?: "house" | "center" | "south" | "west" | "east";
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["BandTimeline"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-19": {
        readonly parameters: {
            readonly query: {
                /** @description America/Denver day; default today */
                readonly day?: string;
                /** @description A crop-band anchor or width: band_<series>_<anchor>, zone_vpd_target_<zone>_<anchor>, zone_vpd_width_below_<zone> or zone_vpd_width_above_<zone> */
                readonly param: string;
                /** @description The draft value, in the setting's unit */
                readonly value: number;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["BandPreview"];
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-13": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Equipment"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-20": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly light: "main" | "grow";
                readonly state: "on" | "off";
            };
            readonly cookie?: never;
        };
        readonly requestBody?: {
            readonly content: {
                readonly "application/json": {
                    readonly reason?: string;
                };
            };
        };
        readonly responses: {
            /** @description Lutron reached the state */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["LightCommand"];
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-12": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["OverrideRequest"];
            };
        };
        readonly responses: {
            readonly 202: components["responses"]["Accepted"];
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-01": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["IrrigationStatus"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-13": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: {
            readonly content: {
                readonly "application/json": components["schemas"]["LineCommand"];
            };
        };
        readonly responses: {
            readonly 202: components["responses"]["Accepted"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-08": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly line_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["LineUpdate"];
            };
        };
        readonly responses: {
            /** @description The changed lines, sync requested */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": {
                        readonly lines: readonly components["schemas"]["IrrigationLine"][];
                    };
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-06": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly line_id: components["parameters"]["LineId"];
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["RunRequest"];
            };
        };
        readonly responses: {
            readonly 202: components["responses"]["Accepted"];
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-10": {
        readonly parameters: {
            readonly query: {
                readonly from: string;
                readonly group_by?: "zone" | "line" | "cause";
                /** @description Inclusive local day */
                readonly to: string;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MinutesReport"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-11": {
        readonly parameters: {
            readonly query: {
                readonly cause?: components["schemas"]["IrrigationCause"];
                /** @description next_cursor from the previous page */
                readonly cursor?: components["parameters"]["Cursor"];
                /** @description Window start (inclusive) */
                readonly from: components["parameters"]["From"];
                readonly limit?: components["parameters"]["Limit"];
                readonly line?: components["schemas"]["LineId"];
                readonly outcome?: components["schemas"]["RunOutcome"];
                /** @description Window end (exclusive) */
                readonly to: components["parameters"]["To"];
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["RunPage"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-02": {
        readonly parameters: {
            readonly query?: {
                /** @description Days ahead from the start of today */
                readonly days?: number;
                /** @description Past days of occurrences with their outcomes */
                readonly past_days?: number;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ScheduleAgenda"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-03": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["ScheduleCreate"];
            };
        };
        readonly responses: {
            /** @description Created, sync requested until the controller's program hash matches */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Schedule"];
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-04": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly slot_id: components["parameters"]["SlotId"];
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["ScheduleInput"];
            };
        };
        readonly responses: {
            /** @description Stored, sync requested */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Schedule"];
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-05": {
        readonly parameters: {
            readonly query?: {
                readonly reason?: components["parameters"]["Reason"];
            };
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                readonly slot_id: components["parameters"]["SlotId"];
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Deleted; the program banner shows the push */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-IRR-07": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: {
            readonly content: {
                readonly "application/json": components["schemas"]["LineCommand"];
            };
        };
        readonly responses: {
            readonly 202: components["responses"]["Accepted"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-01": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Me"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-03": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": {
                        readonly items: readonly components["schemas"]["MetricDef"][];
                    };
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-02": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Now"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-07": {
        readonly parameters: {
            readonly query?: {
                /** @description Lane horizon; live waypoints rarely reach past a few hours */
                readonly hours?: 24 | 72;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Plan"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-04": {
        readonly parameters: {
            readonly query: {
                /** @description Window start (inclusive) */
                readonly from: components["parameters"]["From"];
                /** @description Metric key from API-03; repeat for up to 8 */
                readonly metric: readonly string[];
                readonly points?: number;
                /** @description Window end (exclusive) */
                readonly to: components["parameters"]["To"];
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Series"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-09": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": {
                        readonly items: readonly components["schemas"]["Setting"][];
                        /** Format: date-time */
                        readonly server_time: string;
                    };
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-10": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                /** @description tunable_registry name */
                readonly param: components["parameters"]["Param"];
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["SettingWrite"];
            };
        };
        readonly responses: {
            /** @description Stored, with state requested (or no_readback); snapped_from when snapping changed the value, manual_before when the flag changed */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DesiredSetting"];
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-11": {
        readonly parameters: {
            readonly query?: {
                readonly reason?: components["parameters"]["Reason"];
            };
            readonly header?: {
                /** @description Optional on every write; stored as request_id on the write's activity_log row and logged as the request id. The same key and payload return the stored result with the header Idempotency-Replayed: true; the same key with a different payload is 422 request_id_reused. */
                readonly "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            readonly path: {
                /** @description tunable_registry name */
                readonly param: components["parameters"]["Param"];
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description The release row (value null, state released) */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DesiredSetting"];
                };
            };
            readonly 422: components["responses"]["Invalid"];
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-12": {
        readonly parameters: {
            readonly query: {
                /** @description Window start (inclusive) */
                readonly from: components["parameters"]["From"];
                /** @description Window end (exclusive) */
                readonly to: components["parameters"]["To"];
            };
            readonly header?: never;
            readonly path: {
                /** @description tunable_registry name */
                readonly param: components["parameters"]["Param"];
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["SettingHistory"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-05": {
        readonly parameters: {
            readonly query: {
                /** @description Only these lanes; default all */
                readonly equipment?: readonly string[];
                /** @description Window start (inclusive) */
                readonly from: components["parameters"]["From"];
                /** @description Window end (exclusive) */
                readonly to: components["parameters"]["To"];
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["StateLanes"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
    readonly "API-21": {
        readonly parameters: {
            readonly query?: {
                /** @description Days ending today, today included */
                readonly days?: 30 | 90;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description OK */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["WaterUse"];
                };
            };
            readonly default: components["responses"]["Problem"];
        };
    };
}
