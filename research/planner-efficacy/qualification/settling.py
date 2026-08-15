"""Frozen §8.3 settling-time analyzer (qualification spec v1, #588).

Implements the audit's disturbance-adjusted first-order response fit for the
step-test qualification gate
(docs/research/planner-efficacy-current-firmware-2026-08-14.md §8.3):

- Endpoints per transition: indoor VPD (kPa), indoor temperature (°F), and
  nine-device duty (device-minutes per 15-min bin), each observed over the
  six post-step hours as 24 quarter-hour bins (t = 0.25 .. 6.00 h, bin-end
  offsets from the step).
- Frozen response model per endpoint:

      y_i = y_inf + A * exp(-t_i / tau) + beta * (d_i - mean(d))

  where d is the endpoint's single named disturbance covariate (optional per
  input; the beta term is dropped when absent). The fit minimizes the sum of
  squared residuals over the 24 bins: tau is searched on a frozen geometric
  grid (TAU_GRID_MIN_H..TAU_GRID_MAX_H, TAU_GRID_POINTS points) with the
  linear parameters solved exactly at each tau, then refined by
  TAU_REFINE_ITERATIONS golden-section iterations between the best grid
  point's neighbours. Pure deterministic Python — no numpy/scipy, so the
  frozen fit cannot drift with a library version.

- Settling time: the first 15-minute boundary T after which the fitted
  transient stays within the endpoint's band of its post-step asymptote
  through hour six, i.e. the smallest bin boundary with
  |A| * exp(-T/tau) <= band (the fitted transient is monotone, so staying
  within through hour six follows). Bands (frozen, §8.3): 0.025 kPa,
  0.25 °F, 6.75 device-minutes per bin. No boundary by 6 h => unsettled
  (infinite settling time; fails).

- Gate statistic: the MAXIMUM settling time over all three endpoints and all
  96 fixed transitions — not a mean, quantile, or confidence bound. The gate
  passes iff that maximum is <= 2 h AND policy identity was confirmed within
  120 s in EVERY transition AND every locked response-model diagnostic
  passes. Any missing/short/non-finite trace is an unanalyzable transition
  and fails the gate (§8.3: do not extend or selectively resample).

- Frozen response-model diagnostics (named before collection, §8.3; the
  numeric values are the proposed frozen set, marked TO-LOCK in the spec):
    * fit R^2 >= 0.60 whenever the fitted transient is material
      (|A| > the endpoint band; an immaterial transient is exempt because
      R^2 is undefined-in-practice for a flat response);
    * |lag-1 residual autocorrelation| <= 0.50;
    * conditional 95% CI half-width of the asymptote y_inf <= 1x the
      endpoint band (linear-parameter covariance at the fitted tau,
      t-quantile at the residual degrees of freedom);
    * fitted tau not pinned to the search-grid edge when the transient is
      material (a pinned tau means the first-order form did not fit).

The emitted result is a canonical machine-readable dict; its SHA-256 is the
qualification result hash that fn_experiment_transition binds for the A/A
phase. The repo's RFC 8785 profile (switchback.randomization) rejects
floats/booleans/None by design, and this result necessarily carries all
three, so the frozen hash here is SHA-256 over compact sorted-key JSON
(``json.dumps(result, sort_keys=True, separators=(",", ":"),
ensure_ascii=True, allow_nan=False)``). All reported floats are first
rounded to REPORT_DECIMALS decimals, and Python's shortest-repr float
serialization is deterministic, so the hash is representation-stable.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

ANALYZER_VERSION = "qualification-settling-v1"
RESULT_SCHEMA_VERSION = 1

# --- Frozen endpoint bands (§8.3) ------------------------------------------
ENDPOINT_BANDS: dict[str, float] = {
    "vpd_kpa": 0.025,  # kPa
    "temp_f": 0.25,  # °F
    "duty_devmin": 6.75,  # device-minutes per 15-min bin
}
ENDPOINTS = tuple(ENDPOINT_BANDS)

# --- Frozen protocol constants ---------------------------------------------
POST_STEP_BINS = 24  # six hours of 15-min bins
BIN_HOURS = 0.25
GATE_MAX_SETTLING_H = 2.0
IDENTITY_CONFIRM_MAX_S = 120.0
EXPECTED_TRANSITIONS = 96

# --- Frozen fit parameters --------------------------------------------------
TAU_GRID_MIN_H = 0.02
TAU_GRID_MAX_H = 12.0
TAU_GRID_POINTS = 60
TAU_REFINE_ITERATIONS = 40
TAU_EDGE_PIN_FRACTION = 0.01  # within 1% of a grid edge counts as pinned

# --- Frozen diagnostic thresholds (proposed frozen set — TO-LOCK in spec) ---
DIAG_MIN_R_SQUARED = 0.60
DIAG_MAX_RESID_LAG1_AUTOCORR = 0.50
DIAG_MAX_ASYMPTOTE_CI_BAND_RATIO = 1.0  # CI half-width <= ratio * band

REPORT_DECIMALS = 6

# Two-sided 97.5% Student-t quantiles for the residual dof this analyzer can
# produce (24 bins minus 2 or 3 linear parameters; tau is conditioned on).
_T_975 = {20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069}

_EXPECTED_T_H = tuple(round(BIN_HOURS * (i + 1), 6) for i in range(POST_STEP_BINS))


class AnalyzerInputError(ValueError):
    """The input trace violates the frozen input contract."""


# ============================================================================
# Deterministic linear algebra (tiny, explicit)
# ============================================================================


def _solve_symmetric(ata: list[list[float]], atb: list[float]) -> list[float] | None:
    """Solve (A^T A) x = A^T b by Gaussian elimination with partial pivoting.

    Returns None when the system is numerically singular.
    """
    n = len(atb)
    m = [row[:] + [atb[i]] for i, row in enumerate(ata)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for row in range(n):
            if row == col:
                continue
            factor = m[row][col] / m[col][col]
            for k in range(col, n + 1):
                m[row][k] -= factor * m[col][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _invert_symmetric(ata: list[list[float]]) -> list[list[float]] | None:
    """Inverse of a small symmetric positive-definite matrix (for the
    conditional linear-parameter covariance)."""
    n = len(ata)
    cols = []
    for j in range(n):
        unit = [1.0 if i == j else 0.0 for i in range(n)]
        col = _solve_symmetric(ata, unit)
        if col is None:
            return None
        cols.append(col)
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _design_row(t_h: float, tau_h: float, disturbance: float | None) -> list[float]:
    row = [1.0, math.exp(-t_h / tau_h)]
    if disturbance is not None:
        row.append(disturbance)
    return row


def _fit_at_tau(
    t: list[float], y: list[float], d: list[float] | None, tau_h: float
) -> tuple[list[float], float] | None:
    """Exact linear LS at fixed tau. Returns (params, sse) or None."""
    n_params = 2 + (1 if d is not None else 0)
    ata = [[0.0] * n_params for _ in range(n_params)]
    atb = [0.0] * n_params
    for i, t_i in enumerate(t):
        row = _design_row(t_i, tau_h, d[i] if d is not None else None)
        for a in range(n_params):
            atb[a] += row[a] * y[i]
            for b in range(n_params):
                ata[a][b] += row[a] * row[b]
    params = _solve_symmetric(ata, atb)
    if params is None:
        return None
    sse = 0.0
    for i, t_i in enumerate(t):
        row = _design_row(t_i, tau_h, d[i] if d is not None else None)
        pred = sum(p * r for p, r in zip(params, row, strict=True))
        sse += (y[i] - pred) ** 2
    return params, sse


def _tau_grid() -> list[float]:
    ratio = (TAU_GRID_MAX_H / TAU_GRID_MIN_H) ** (1.0 / (TAU_GRID_POINTS - 1))
    return [TAU_GRID_MIN_H * ratio**i for i in range(TAU_GRID_POINTS)]


_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


def fit_first_order(t: list[float], y: list[float], disturbance: list[float] | None) -> dict[str, Any] | None:
    """Frozen fit: grid search over tau + golden-section refinement.

    Returns fit dict or None when every tau is singular (degenerate input).
    """
    if disturbance is not None:
        mean_d = sum(disturbance) / len(disturbance)
        d = [v - mean_d for v in disturbance]
    else:
        d = None

    grid = _tau_grid()
    best_idx, best_sse, best_params = None, math.inf, None
    for idx, tau_h in enumerate(grid):
        fit = _fit_at_tau(t, y, d, tau_h)
        if fit is None:
            continue
        params, sse = fit
        if sse < best_sse - 1e-15:
            best_idx, best_sse, best_params = idx, sse, params
    if best_idx is None:
        return None

    lo = grid[max(0, best_idx - 1)]
    hi = grid[min(len(grid) - 1, best_idx + 1)]
    a, b = lo, hi
    x1 = b - _GOLDEN * (b - a)
    x2 = a + _GOLDEN * (b - a)
    f1 = _fit_at_tau(t, y, d, x1)
    f2 = _fit_at_tau(t, y, d, x2)
    for _ in range(TAU_REFINE_ITERATIONS):
        s1 = f1[1] if f1 is not None else math.inf
        s2 = f2[1] if f2 is not None else math.inf
        if s1 <= s2:
            b, x2, f2 = x2, x1, f1
            x1 = b - _GOLDEN * (b - a)
            f1 = _fit_at_tau(t, y, d, x1)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + _GOLDEN * (b - a)
            f2 = _fit_at_tau(t, y, d, x2)
    for tau_h, fit in ((x1, f1), (x2, f2), (grid[best_idx], (best_params, best_sse))):
        if fit is not None and fit[1] <= best_sse:
            best_sse = fit[1]
            best_params = fit[0]
            best_tau = tau_h
    # (grid best is always a candidate, so best_tau is always bound)

    params = best_params
    n = len(y)
    n_params = len(params)
    dof = n - n_params
    mean_y = sum(y) / n
    sst = sum((v - mean_y) ** 2 for v in y)
    r_squared = 1.0 - best_sse / sst if sst > 0 else 1.0

    residuals = []
    for i, t_i in enumerate(t):
        row = _design_row(t_i, best_tau, d[i] if d is not None else None)
        residuals.append(y[i] - sum(p * r for p, r in zip(params, row, strict=True)))
    num = sum(residuals[i] * residuals[i + 1] for i in range(n - 1))
    den = sum(r * r for r in residuals)
    lag1 = num / den if den > 1e-18 else 0.0

    # Conditional (fixed-tau) covariance of the linear parameters.
    ata = [[0.0] * n_params for _ in range(n_params)]
    for i, t_i in enumerate(t):
        row = _design_row(t_i, best_tau, d[i] if d is not None else None)
        for a_i in range(n_params):
            for b_i in range(n_params):
                ata[a_i][b_i] += row[a_i] * row[b_i]
    inv = _invert_symmetric(ata)
    sigma2 = best_sse / dof if dof > 0 else 0.0
    if inv is not None and dof in _T_975:
        ci_half_width = _T_975[dof] * math.sqrt(max(0.0, sigma2 * inv[0][0]))
    else:
        ci_half_width = math.inf

    return {
        "tau_h": best_tau,
        "asymptote": params[0],
        "amplitude": params[1],
        "beta": params[2] if n_params == 3 else None,
        "sse": best_sse,
        "r_squared": r_squared,
        "resid_lag1_autocorr": lag1,
        "asymptote_ci_half_width": ci_half_width,
        "dof": dof,
    }


def settling_time_h(amplitude: float, tau_h: float, band: float) -> float | None:
    """First 15-min boundary after which |A|exp(-t/tau) stays within `band`
    through hour six; None when no boundary settles."""
    for t_h in _EXPECTED_T_H:
        if abs(amplitude) * math.exp(-t_h / tau_h) <= band:
            return t_h
    return None


# ============================================================================
# Transition / endpoint analysis
# ============================================================================


def _validate_bins(bins: list[dict]) -> tuple[list[float], list[float], list[float] | None]:
    if len(bins) != POST_STEP_BINS:
        raise AnalyzerInputError(f"expected {POST_STEP_BINS} post-step bins, got {len(bins)}")
    t: list[float] = []
    y: list[float] = []
    d: list[float] = []
    has_d = "disturbance" in bins[0]
    for i, item in enumerate(bins):
        t_h = float(item["t_h"])
        if abs(t_h - _EXPECTED_T_H[i]) > 1e-9:
            raise AnalyzerInputError(f"bin {i} at t_h={t_h}, expected {_EXPECTED_T_H[i]}")
        value = float(item["value"])
        if not math.isfinite(value):
            raise AnalyzerInputError(f"non-finite value in bin {i}")
        if has_d != ("disturbance" in item):
            raise AnalyzerInputError("disturbance column must be present in all bins or none")
        if has_d:
            dist = float(item["disturbance"])
            if not math.isfinite(dist):
                raise AnalyzerInputError(f"non-finite disturbance in bin {i}")
            d.append(dist)
        t.append(_EXPECTED_T_H[i])
        y.append(value)
    return t, y, (d if has_d else None)


def _round(value: float | None, decimals: int = REPORT_DECIMALS) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, decimals)


def analyze_endpoint(endpoint: str, bins: list[dict]) -> dict[str, Any]:
    band = ENDPOINT_BANDS[endpoint]
    failures: list[str] = []
    try:
        t, y, d = _validate_bins(bins)
    except AnalyzerInputError as exc:
        return {
            "endpoint": endpoint,
            "band": band,
            "analyzable": False,
            "failures": [f"unanalyzable: {exc}"],
            "pass": False,
        }
    fit = fit_first_order(t, y, d)
    if fit is None:
        return {
            "endpoint": endpoint,
            "band": band,
            "analyzable": False,
            "failures": ["unanalyzable: degenerate fit"],
            "pass": False,
        }
    settling = settling_time_h(fit["amplitude"], fit["tau_h"], band)
    if settling is None:
        failures.append("unsettled_within_6h")
    elif settling > GATE_MAX_SETTLING_H:
        failures.append("settling_over_2h")

    material = abs(fit["amplitude"]) > band
    diagnostics = {
        "material_transient": material,
        "r_squared_ok": (not material) or fit["r_squared"] >= DIAG_MIN_R_SQUARED,
        "resid_lag1_ok": abs(fit["resid_lag1_autocorr"]) <= DIAG_MAX_RESID_LAG1_AUTOCORR,
        "asymptote_ci_ok": fit["asymptote_ci_half_width"] <= DIAG_MAX_ASYMPTOTE_CI_BAND_RATIO * band,
        "tau_not_pinned": (not material)
        or (
            fit["tau_h"] > TAU_GRID_MIN_H * (1 + TAU_EDGE_PIN_FRACTION)
            and fit["tau_h"] < TAU_GRID_MAX_H * (1 - TAU_EDGE_PIN_FRACTION)
        ),
    }
    for name in ("r_squared_ok", "resid_lag1_ok", "asymptote_ci_ok", "tau_not_pinned"):
        if not diagnostics[name]:
            failures.append(f"diagnostic:{name}")

    return {
        "endpoint": endpoint,
        "band": band,
        "analyzable": True,
        "settling_h": settling,
        "tau_h": _round(fit["tau_h"]),
        "asymptote": _round(fit["asymptote"]),
        "amplitude": _round(fit["amplitude"]),
        "beta": _round(fit["beta"]) if fit["beta"] is not None else None,
        "r_squared": _round(fit["r_squared"]),
        "resid_lag1_autocorr": _round(fit["resid_lag1_autocorr"]),
        "asymptote_ci_half_width": _round(fit["asymptote_ci_half_width"]),
        "diagnostics": diagnostics,
        "failures": failures,
        "pass": not failures,
    }


def analyze_transition(transition: dict) -> dict[str, Any]:
    failures: list[str] = []
    endpoints_out = []
    endpoints_in = transition.get("endpoints") or {}
    for endpoint in ENDPOINTS:
        if endpoint not in endpoints_in:
            endpoints_out.append(
                {
                    "endpoint": endpoint,
                    "band": ENDPOINT_BANDS[endpoint],
                    "analyzable": False,
                    "failures": ["unanalyzable: endpoint missing"],
                    "pass": False,
                }
            )
            failures.append(f"endpoint_missing:{endpoint}")
            continue
        result = analyze_endpoint(endpoint, endpoints_in[endpoint].get("bins") or [])
        endpoints_out.append(result)
        failures.extend(f"{endpoint}:{f}" for f in result["failures"])

    identity_s = transition.get("identity_confirm_s")
    identity_ok = (
        identity_s is not None and math.isfinite(float(identity_s)) and float(identity_s) <= IDENTITY_CONFIRM_MAX_S
    )
    if not identity_ok:
        failures.append("identity_not_confirmed_within_120s")

    settlings = [e.get("settling_h") for e in endpoints_out if e.get("settling_h") is not None]
    return {
        "transition_id": transition.get("transition_id"),
        "slot_id": transition.get("slot_id"),
        "cell_index": transition.get("cell_index"),
        "edge": transition.get("edge"),
        "regime": transition.get("regime"),
        "step_time_utc": transition.get("step_time_utc"),
        "identity_confirm_s": _round(float(identity_s)) if identity_s is not None else None,
        "identity_ok": identity_ok,
        "endpoints": endpoints_out,
        "max_settling_h": max(settlings) if settlings else None,
        "failures": failures,
        "pass": not failures,
    }


def analyze(payload: dict, expected_transitions: int = EXPECTED_TRANSITIONS) -> dict[str, Any]:
    """Analyze the full transition set and emit the hashed gate result."""
    transitions = [analyze_transition(t) for t in payload.get("transitions") or []]
    gate_failures: list[str] = []
    if len(transitions) != expected_transitions:
        gate_failures.append(f"transition_count:{len(transitions)}_of_{expected_transitions}")
    unanalyzable = [t["transition_id"] for t in transitions if not all(e["analyzable"] for e in t["endpoints"])]
    if unanalyzable:
        gate_failures.append("unanalyzable_transitions")
    settlings = [t["max_settling_h"] for t in transitions if t["max_settling_h"] is not None]
    unsettled = any(e.get("settling_h") is None and e.get("analyzable") for t in transitions for e in t["endpoints"])
    max_settling = max(settlings) if settlings else None
    if unsettled:
        gate_failures.append("unsettled_endpoint")
    if max_settling is not None and max_settling > GATE_MAX_SETTLING_H:
        gate_failures.append("max_settling_over_2h")
    identity_values = [t["identity_confirm_s"] for t in transitions if t["identity_confirm_s"] is not None]
    if not all(t["identity_ok"] for t in transitions):
        gate_failures.append("identity_gate")
    diag_failed = [
        t["transition_id"]
        for t in transitions
        if any(f.split(":", 1)[-1].startswith("diagnostic:") for f in t["failures"])
    ]
    if diag_failed:
        gate_failures.append("diagnostics_failed")

    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "analyzer": ANALYZER_VERSION,
        "study_id": payload.get("study_id"),
        "spec_sha256": payload.get("spec_sha256"),
        "thresholds": {
            "endpoint_bands": ENDPOINT_BANDS,
            "gate_max_settling_h": GATE_MAX_SETTLING_H,
            "identity_confirm_max_s": IDENTITY_CONFIRM_MAX_S,
            "post_step_bins": POST_STEP_BINS,
        },
        "diagnostics_spec": {
            "min_r_squared": DIAG_MIN_R_SQUARED,
            "max_resid_lag1_autocorr": DIAG_MAX_RESID_LAG1_AUTOCORR,
            "max_asymptote_ci_band_ratio": DIAG_MAX_ASYMPTOTE_CI_BAND_RATIO,
            "tau_grid": [TAU_GRID_MIN_H, TAU_GRID_MAX_H, TAU_GRID_POINTS],
            "tau_refine_iterations": TAU_REFINE_ITERATIONS,
            "tau_edge_pin_fraction": TAU_EDGE_PIN_FRACTION,
        },
        "transitions": transitions,
        "gate": {
            "expected_transitions": expected_transitions,
            "observed_transitions": len(transitions),
            "max_settling_h": max_settling,
            "max_identity_confirm_s": max(identity_values) if identity_values else None,
            "failures": gate_failures,
            "pass": not gate_failures,
        },
    }
    return {"result": result, "result_sha256": result_sha256(result)}


def result_sha256(result: dict) -> str:
    """Frozen result hash: SHA-256 over compact sorted-key JSON (see module
    docstring for why the repo's RFC 8785 int/str profile is not usable)."""
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
