#!/usr/bin/env python3
"""Generate the public Lessons Learned page from planner_lessons table.

Output: /srv/verdify/verdify-site/content/reference/lessons.md
"""

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from verdify_schemas import LessonsVaultFrontmatter  # noqa: E402

DB_CONTAINER = "verdify-timescaledb"
DB_USER = "verdify"
DB_NAME = "verdify"
OUTPUT_PATH = "/srv/verdify/verdify-site/content/reference/lessons.md"
RAW_OUTPUT_PATH = "/srv/verdify/state/site-generated/raw-planner-lessons.md"


def query_db(sql: str) -> str:
    """Run a SQL query via docker exec and return raw output."""
    result = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-t", "-A", "-F", "\t", "-c", sql],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"DB error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def plan_id_to_link(plan_id: str) -> str:
    """Convert a plan_id like 'iris-20260325-1201' to a markdown link.

    URL: /plans/2026-03-25 (no trailing slash)
    Label: the plan_id itself
    """
    m = re.match(r"iris-(\d{4})(\d{2})(\d{2})", plan_id)
    if not m:
        return plan_id  # Can't parse — return as plain text
    plan_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"[{plan_id}](/plans/{plan_date})"


def public_text(text: str) -> str:
    """Avoid Quartz/Markdown dollar-sign math parsing on public pages."""
    return re.sub(r"\$(\d)", r"USD \1", text or "")


DLI_SOURCE_RE = re.compile(
    r"sensor[_ -]*dli|dli[_ -]*(?:today|sensor)|"
    r"interior[^.]{0,40}dli|crop[^.]{0,40}dli",
    re.IGNORECASE,
)
DLI_INFERENCE_RE = re.compile(
    r"(?:×|\*|\sx\s)\s*\d|correction|corrected|proxy|estimate|estimated|"
    r"multiply|multiplied|factor|grow[_ -]*light[_ -]*(?:hours|runtime)",
    re.IGNORECASE,
)


def is_invalid_dli_proxy_lesson(lesson: dict) -> bool:
    """Reject lessons that infer crop DLI from the broken sensor or proxies."""
    text = f"{lesson.get('condition', '')} {lesson.get('lesson', '')}"
    return bool(DLI_SOURCE_RE.search(text) and DLI_INFERENCE_RE.search(text))


def fetch_lessons(active: bool) -> list[dict]:
    """Fetch lessons from the database."""
    flag = "true" if active else "false"
    sql = f"""
        SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json)
        FROM (
            SELECT id, category, condition, lesson, confidence,
                   times_validated, source_plan_ids,
                   created_at::date::text AS created_at,
                   last_validated::date::text AS last_validated,
                   superseded_by
            FROM planner_lessons
            WHERE is_active = {flag}
            ORDER BY id
        ) t;
    """
    raw = query_db(sql)
    if not raw:
        return []

    lessons = []
    for row in json.loads(raw):
        lessons.append(
            {
                "id": int(row["id"]),
                "category": row["category"],
                "condition": row["condition"],
                "lesson": row["lesson"],
                "confidence": row["confidence"],
                "times_validated": int(row["times_validated"]),
                "source_plan_ids": row["source_plan_ids"] or [],
                "created_at": row["created_at"] or "",
                "last_validated": row["last_validated"] or "",
                "superseded_by": int(row["superseded_by"]) if row["superseded_by"] is not None else None,
            }
        )
    # Defense in depth: both active guidance and the generated noindex audit
    # omit invalid DLI proxy/correction text. The original DB row remains
    # preserved and inactive for direct forensic inspection.
    return [lesson for lesson in lessons if not is_invalid_dli_proxy_lesson(lesson)]


CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
PARAM_TOKEN_RE = re.compile(
    r"\b(?:bias_cool|bias_heat|mist_max_closed_vent_s|mister_engage_kpa|mister_all_kpa|"
    r"fog_escalation_kpa|fog_min_temp_f|vpd_high|vpd_low|temp_high|temp_low|"
    r"d_cool_stage_2|cool_stage2_over_high_f|cool_exit_hysteresis_f|"
    r"cold_vent_guard_delta_f|sw_cool_all_fans_at_high_enabled|"
    r"sw_direct_wet_stress_override_enabled|direct_wet_stress_vpd_margin_kpa|"
    r"direct_wet_stress_min_dew_margin_f|direct_wet_stress_latest_hour|"
    r"sw_fog_stress_window_extend_enabled|fog_stress_window_latest_hour|"
    r"fog_stress_min_dew_margin_f|"
    r"enthalpy_open|enthalpy_close|mist_vent_close_lead_s)\b",
    re.IGNORECASE,
)


CURATED_SECTIONS = [
    {
        "title": "Heat With Gas; Let Thermal Mass Work",
        "lesson_ids": [2, 91, 96, 87],
        "rules": [
            "Use electric heat for mild dips and gas heat for sustained cold; gas remains the efficient overnight BTU source.",
            "Do not chase every spring-morning cold-stress hour with higher heat setpoints. Some score loss comes from crop-band alignment, not plant danger.",
            "Plan around a 62-66F overnight equilibrium on 48-55F spring nights, especially when humidity can sag VPD below band before dawn.",
        ],
    },
    {
        "title": "Cooling Is Physics-Limited",
        "lesson_ids": [4, 89, 95],
        "rules": [
            "Shade cloth is the real hot-day fix; software can pre-cool, mist, and ventilate, but cannot erase 90F+ solar gain.",
            "Do not seal the house for humidity trapping when indoor temperature is near the safety ceiling.",
            "VENTILATE plus misting is the safe outcome when sealed misting cannot overcome solar load.",
        ],
    },
    {
        "title": "Tune Misting To Weather Regime",
        "lesson_ids": [1, 10, 27, 88, 90, 93, 98, 99, 100, 101, 102],
        "rules": [
            "Reserve aggressive misting for warm, dry, high-VPD days. Cold-dry air can still produce manageable indoor VPD.",
            "Use moderate mist/fog settings on moderate-dry days to avoid VPD-low overshoot.",
            "On cool, cloudy, high-RH days, suppress mist and favor ventilation/dehumidification rather than water-use optimization alone.",
            "Treat the 900s closed-vent experiment as an extreme-dry tool, not a general humidity strategy.",
        ],
    },
    {
        "title": "Keep Planner Control Bounded",
        "lesson_ids": [5, 7, 8, 94, 97],
        "rules": [
            "Use canonical DB parameter names; ESP32 object IDs create duplicate active-plan rows.",
            "Never set timer parameters to zero. Firmware minimums exist to avoid relay chatter.",
            "Do not push unconfirmed zone-specific VPD targets. Use firmware-confirmed global misting tunables instead.",
            "Replace stale plans when the forecast regime changes, even if the original plan window has not expired.",
        ],
    },
    {
        "title": "Read Scores Through Structural Constraints",
        "lesson_ids": [87, 91, 92],
        "rules": [
            "Some cold, heat, and combined compliance loss is structural when crop bands are narrower than the greenhouse's spring operating envelope.",
            "Use VPD compliance as the meaningful optimization axis on warm spring days where low temperature compliance is physically unreachable.",
            "Separate controller failures from crop-band scoring artifacts before changing tunables.",
        ],
    },
]


TOPIC_LABELS = {
    "gas_heat_economics": "Gas heat economics",
    "cooling_physics_limit": "Cooling physics limit",
    "band_temp_high_dispatcher": "Dispatcher owns temp_high",
    "canonical_parameter_names": "Canonical parameter names",
    "timer_zero_safety": "Timer zero safety",
    "dry_day_misting": "Warm-dry misting posture",
    "cold_dry_misting": "Cold-dry misting posture",
    "mist_900_extreme_dry": "900s closed-vent extreme-dry experiment",
    "mild_standard_misting": "Mild-day baseline misting",
    "mild_misting_reboot_split": "Mild-day misting plus reboot/setpoint split",
    "tight_crop_bands": "Tight crop-band misting and heat bias",
    "cold_night_bias_fog": "Cold-night bias and moderate-day fog tuning",
    "structural_overnight_heat": "Structural overnight heat score loss",
    "moderate_dry_fog_overshoot": "Moderate-dry fog overshoot",
    "sealed_mist_thermal_risk": "Sealed mist thermal risk",
    "moderate_warm_overmist": "Moderate-warm over-misting",
    "crop_band_morning_cold": "Morning crop-band cold scoring",
    "crop_band_warm_upper": "Warm-side crop-band scoring",
    "overcast_cold_misting": "Overcast cold misting",
    "unconfirmed_zone_vpd_targets": "Unconfirmed zone VPD targets",
    "sealed_mist_cycles_to_ventilate": "Sealed mist cycles to ventilation",
    "slab_overnight_equilibrium": "Slab overnight equilibrium",
    "stale_plan_regime_change": "Stale plan regime changes",
    "cool_high_rh_recovery": "Cool high-RH recovery posture",
    "mist_suppression_needs_ventilation": "Mist suppression plus ventilation",
    "cool_cloudy_no_dry_posture": "Do not carry dry posture into cool/cloudy periods",
    "cool_cloudy_conservative_fog": "Cool cloudy conservative fog",
    "cloudy_humid_dry_ramp": "Cloudy high-humidity dry-ramp avoidance",
    "water_budget_zero": "Mister water budget zero safety",
    "vent_fog_defaults": "Vent/fog defaults",
    "forecast_deviation_replan": "Forecast deviation replanning",
    "weekend_dry_experiment_pending": "Weekend dry experiment placeholder",
    "overnight_bias_cool": "Overnight bias_cool oscillation guard",
    "dispatcher_reboot_correction": "Dispatcher reboot correction",
    "internal_test_row": "Internal test row",
}


TOPIC_SUMMARIES = {
    "mist_900_extreme_dry": (
        "Extreme-dry evidence supports a longer closed-vent mist window, but superseded duplicates are historical "
        "confirmations rather than separate guidance."
    ),
    "mild_standard_misting": (
        "Baseline misting around engage 1.4-1.5 with 30-35s gaps works on mild, moderate-RH days; reserve stronger "
        "settings for genuinely dry VPD pressure."
    ),
    "mild_misting_reboot_split": (
        "Mixed extraction: the mild-day misting result is separate from reboot/setpoint cold-stress evidence. Do not "
        "read this as one causal axis."
    ),
    "cold_night_bias_fog": (
        "Repeated confirmations collapse to one signal: cold nights need heat/cool bias coordination, while moderate "
        "days need fog_escalation around 0.4 to avoid VPD-low overshoot."
    ),
    "tight_crop_bands": (
        "When crop bands are unusually tight, misting must engage below the band ceiling and heat periods need cooling "
        "bias to avoid oscillation."
    ),
    "cool_high_rh_recovery": (
        "Cool, cloudy, high-RH recovery days are VPD-low and condensation problems; suppress mist and use ventilation "
        "when dew-point margin allows."
    ),
    "cloudy_humid_dry_ramp": (
        "When cloud cover and outdoor humidity are both high, avoid dry-ramp misting posture and raise mist thresholds "
        "early enough to prevent VPD-low accumulation."
    ),
    "internal_test_row": "Internal create-path test retained only for DB audit; not operational guidance.",
}


def lesson_signature(lesson: dict) -> str:
    """Stable-ish key for collapsing repeated machine-extracted lesson rows."""
    topic = lesson_topic(lesson)
    if topic:
        return topic

    raw = normalize_lesson_text(f"{lesson['category']} {lesson['condition']} {lesson['lesson']}")
    raw = re.sub(r"\b\d+(?:st|nd|rd|th)?\b", "#", raw)
    raw = re.sub(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|nth)\b",
        "#",
        raw,
    )
    raw = re.sub(r"\b(confirm(?:ed|ation|ations)?|validated|validation)\b", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    params = sorted({p.lower() for p in PARAM_TOKEN_RE.findall(raw)})
    if params:
        return f"{lesson['category'].lower()}|{','.join(params)}|{raw[:120]}"
    return f"{lesson['category'].lower()}|{raw[:140]}"


def normalize_lesson_text(text: str) -> str:
    """Normalize machine text before grouping or rendering."""
    raw = (text or "").lower()
    raw = re.sub(r"\b\d+(?:st|nd|rd|th)?\b", "#", raw)
    raw = re.sub(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|nth)\b",
        "#",
        raw,
    )
    raw = re.sub(r"\([^)]*confirmation[^)]*\)", "", raw)
    raw = re.sub(r"\b(confirm(?:ed|ation|ations)?|validated|validation|strongly)\b", "", raw)
    raw = re.sub(r"score # exceeded target of # dramatically(?: — previous targets were too conservative)?", "", raw)
    raw = re.sub(r"cost (?:of )?\$?#(?:\.\d+)?(?: is)? acceptable[^.]*\.", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def lesson_topic(lesson: dict) -> str | None:
    """Return curated grouping key for known noisy lesson families."""
    lesson_id = lesson["id"]
    text = normalize_lesson_text(f"{lesson['condition']} {lesson['lesson']}")

    id_topics = {
        1: "dry_day_misting",
        2: "gas_heat_economics",
        4: "cooling_physics_limit",
        5: "band_temp_high_dispatcher",
        6: "dispatcher_reboot_correction",
        7: "canonical_parameter_names",
        8: "timer_zero_safety",
        9: "overcast_cold_misting",
        10: "cold_dry_misting",
        11: "water_budget_zero",
        15: "forecast_deviation_replan",
        21: "forecast_deviation_replan",
        25: "vent_fog_defaults",
        42: "overnight_bias_cool",
        50: "mild_misting_reboot_split",
        86: "internal_test_row",
        87: "structural_overnight_heat",
        88: "moderate_dry_fog_overshoot",
        89: "sealed_mist_thermal_risk",
        90: "moderate_warm_overmist",
        91: "crop_band_morning_cold",
        92: "crop_band_warm_upper",
        93: "overcast_cold_misting",
        94: "unconfirmed_zone_vpd_targets",
        95: "sealed_mist_cycles_to_ventilate",
        96: "slab_overnight_equilibrium",
        97: "stale_plan_regime_change",
        98: "cool_high_rh_recovery",
        99: "mist_suppression_needs_ventilation",
        100: "cool_cloudy_no_dry_posture",
        101: "cool_cloudy_conservative_fog",
        102: "cloudy_humid_dry_ramp",
    }
    if lesson_id in id_topics:
        return id_topics[lesson_id]
    if 66 <= lesson_id <= 84:
        return "cold_night_bias_fog"
    if "mist_max_closed_vent_s" in text and "900s" in text:
        return "mist_900_extreme_dry"
    if "900s max closed vent strategy" in text:
        return "mist_900_extreme_dry"
    if "maintain the experiment targeting weekend dry" in text or "pending execution" in text:
        return "weekend_dry_experiment_pending"
    if "standard misting" in text and "30.87h cold_stress" in text:
        return "mild_misting_reboot_split"
    if "standard misting" in text and "mild day" in text:
        return "mild_standard_misting"
    if "tight crop bands" in text or "band ceiling" in text:
        return "tight_crop_bands"
    if "bias_cool +3" in text and "bias_heat +1" in text and "cold nights" in text:
        return "cold_night_bias_fog"
    return None


def canonicalize_lessons(lessons: list[dict], limit: int = 20) -> list[dict]:
    """Collapse near-duplicate active lessons into public-readable canonical rows."""
    groups: dict[str, list[dict]] = {}
    for lesson in lessons:
        groups.setdefault(lesson_signature(lesson), []).append(lesson)

    canonical: list[dict] = []
    for group in groups.values():
        source_ids: list[str] = []
        seen_sources: set[str] = set()
        for lesson in group:
            for plan_id in lesson["source_plan_ids"]:
                if plan_id not in seen_sources:
                    seen_sources.add(plan_id)
                    source_ids.append(plan_id)

        best = max(
            group,
            key=lambda lesson_row: (
                CONFIDENCE_RANK.get(lesson_row["confidence"].lower(), 0),
                lesson_row["times_validated"],
                len(lesson_row["source_plan_ids"]),
                -lesson_row["id"],
            ),
        ).copy()
        duplicate_ids = sorted(lesson_row["id"] for lesson_row in group)
        best["source_plan_ids"] = source_ids or best["source_plan_ids"]
        best["times_validated"] = max(
            best["times_validated"],
            len(source_ids),
            sum(max(1, lesson_row["times_validated"]) for lesson_row in group),
        )
        best["duplicate_ids"] = duplicate_ids
        best["duplicate_count"] = len(group)
        canonical.append(best)

    canonical.sort(
        key=lambda lesson_row: (
            -CONFIDENCE_RANK.get(lesson_row["confidence"].lower(), 0),
            -lesson_row["times_validated"],
            lesson_row["id"],
        )
    )
    return canonical[:limit]


def first_sentence(text: str, max_len: int = 180) -> str:
    """Return compact public text for audit tables."""
    cleaned = re.sub(r"\s+", " ", public_text(text)).strip()
    sentence_match = re.search(r"(?<=[.!?])\s", cleaned)
    if sentence_match and sentence_match.start() <= max_len:
        return cleaned[: sentence_match.start()].strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def topic_label(lesson: dict) -> str:
    """Human label for a lesson or lesson group."""
    signature = lesson_signature(lesson)
    if signature in TOPIC_LABELS:
        return TOPIC_LABELS[signature]
    return first_sentence(lesson["lesson"], max_len=90)


def operational_summary(lesson: dict) -> str:
    """Curated summary for public operational reading."""
    signature = lesson_signature(lesson)
    if signature in TOPIC_SUMMARIES:
        return TOPIC_SUMMARIES[signature]
    return first_sentence(lesson["lesson"])


def format_id_range(ids: list[int]) -> str:
    """Format raw lesson IDs without hash prefixes so Quartz does not turn them into tags."""
    if not ids:
        return ""
    ranges = []
    start = previous = ids[0]
    for lesson_id in ids[1:]:
        if lesson_id == previous + 1:
            previous = lesson_id
            continue
        ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
        start = previous = lesson_id
    ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def evidence_label(ids: list[int], by_id: dict[int, dict]) -> str:
    """Render compact evidence references for curated sections."""
    refs = []
    for lesson_id in ids:
        lesson = by_id.get(lesson_id)
        if not lesson:
            refs.append(f"lesson {lesson_id}")
            continue
        confidence = lesson["confidence"].capitalize()
        refs.append(f"lesson {lesson_id} ({confidence}, {lesson['times_validated']}x)")
    return ", ".join(refs)


def group_lessons(lessons: list[dict]) -> list[dict]:
    """Collapse lessons into topic groups for the public page and audit trail."""
    groups: dict[str, list[dict]] = {}
    for lesson in lessons:
        groups.setdefault(lesson_signature(lesson), []).append(lesson)

    grouped = []
    for signature, rows in groups.items():
        sorted_rows = sorted(rows, key=lambda row: row["id"])
        active_count = sum(1 for row in sorted_rows if row.get("is_active", False))
        superseded_count = len(sorted_rows) - active_count
        source_ids: list[str] = []
        seen_sources: set[str] = set()
        for row in sorted_rows:
            for plan_id in row["source_plan_ids"]:
                if plan_id not in seen_sources:
                    seen_sources.add(plan_id)
                    source_ids.append(plan_id)

        best = max(
            sorted_rows,
            key=lambda row: (
                1 if row.get("is_active", False) else 0,
                CONFIDENCE_RANK.get(row["confidence"].lower(), 0),
                row["times_validated"],
                len(row["source_plan_ids"]),
                -row["id"],
            ),
        )
        grouped.append(
            {
                "signature": signature,
                "best": best,
                "rows": sorted_rows,
                "ids": [row["id"] for row in sorted_rows],
                "active_count": active_count,
                "superseded_count": superseded_count,
                "times_validated": sum(max(1, row["times_validated"]) for row in sorted_rows),
                "source_plan_ids": source_ids,
            }
        )

    grouped.sort(
        key=lambda group: (
            -group["active_count"],
            -CONFIDENCE_RANK.get(group["best"]["confidence"].lower(), 0),
            -group["times_validated"],
            group["ids"][0],
        )
    )
    return grouped


def render_operational_playbook(active: list[dict]) -> list[str]:
    """Render curated operating knowledge before any machine audit material."""
    by_id = {lesson["id"]: lesson for lesson in active}
    lines = ["## Operational Playbook", ""]
    lines.append(
        "These are the operating rules the AI planning agent should read first. "
        "They are curated from validated lessons; machine extraction details are intentionally pushed into audit tables."
    )
    lines.append("")
    for section in CURATED_SECTIONS:
        lines.append(f"### {section['title']}")
        lines.append("")
        for rule in section["rules"]:
            lines.append(f"- {rule}")
        lines.append("")
        lines.append(f"Evidence: {evidence_label(section['lesson_ids'], by_id)}.")
        lines.append("")
    return lines


def render_current_register(active_groups: list[dict]) -> list[str]:
    """Render active lessons as compact curated register rows."""
    lines = ["## Current Validated Signals", ""]
    lines.append(
        "Active machine lessons are collapsed by operational signal. "
        "Duplicate confirmations count as evidence, not separate public guidance."
    )
    lines.append("")
    lines.append("| Signal | Operational reading | Evidence |")
    lines.append("|---|---|---|")
    for group in active_groups:
        best = group["best"]
        signal = topic_label(best)
        summary = operational_summary(best)
        confidence = best["confidence"].capitalize()
        validation = f"{group['times_validated']}x"
        ids = format_id_range(group["ids"])
        evidence = f"{confidence}; {validation}; lesson rows {ids}"
        lines.append(f"| {signal} | {summary} | {evidence} |")
    lines.append("")
    return lines


def render_audit_trail(all_groups: list[dict], all_lessons: list[dict]) -> list[str]:
    """Render the raw audit trail as collapsed, de-emphasized tables."""
    lines = ["## Machine Extraction Audit", ""]
    lines.append(
        "The raw planner_lessons stream is preserved here for traceability. "
        "It is not the reading order for operations; row-level dumps stay off the public reading path."
    )
    lines.append("")
    lines.append("<details>")
    lines.append(
        f"<summary>Grouped audit trail: {len(all_lessons)} raw rows collapsed into {len(all_groups)} signals</summary>"
    )
    lines.append("")
    lines.append("| Rows | Status | Signal | Audit note |")
    lines.append("|---|---|---|---|")
    for group in all_groups:
        best = group["best"]
        status_parts = []
        if group["active_count"]:
            status_parts.append(f"{group['active_count']} active")
        if group["superseded_count"]:
            status_parts.append(f"{group['superseded_count']} retired")
        status = ", ".join(status_parts)
        note = operational_summary(best)
        lines.append(f"| {format_id_range(group['ids'])} | {status} | {topic_label(best)} | {note} |")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines


def render_audit_boundary(all_groups: list[dict], all_lessons: list[dict]) -> list[str]:
    """Summarize raw lesson audit coverage without publishing the row dump."""
    return [
        "## Audit Boundary",
        "",
        '<div class="data-table">',
        (
            '  <div class="data-row"><strong>Raw lesson stream</strong>'
            f"<span>{len(all_lessons)} rows collapsed into {len(all_groups)} signals</span>"
            "<p>The generator still writes the row-level audit file for operations, but this public page keeps the reading path to curated rules and validated signals.</p></div>"
        ),
        "</div>",
        "",
    ]


def build_lesson_sets() -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Fetch and group lesson rows once for both public and raw pages."""
    active = fetch_lessons(active=True)
    superseded = fetch_lessons(active=False)
    for lesson in active:
        lesson["is_active"] = True
    for lesson in superseded:
        lesson["is_active"] = False
    active_groups = group_lessons(active)
    all_lessons = active + superseded
    all_groups = group_lessons(all_lessons)
    return active, superseded, active_groups, all_lessons, all_groups


def generate_page() -> str:
    """Generate the public lessons.md content."""
    active, superseded, active_groups, all_lessons, all_groups = build_lesson_sets()
    today = date.today().isoformat()

    parts = []

    # Sprint 22: frontmatter validated through LessonsVaultFrontmatter
    fm = LessonsVaultFrontmatter(
        date=date.today(),
        tags=["greenhouse", "planning", "lessons"],
        aliases=["greenhouse/lessons", "intelligence/lessons", "operations/lessons-learned"],
    )
    yaml_block = yaml.safe_dump(
        fm.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        default_flow_style=None,
    )
    yaml_block = re.sub(r"^title: .*$", "title: AI Greenhouse Lessons Learned", yaml_block, flags=re.MULTILINE)
    yaml_block += (
        "description: \"Generated and validated lessons from Verdify's AI greenhouse planning cycles: "
        'what worked, what failed, and what the AI planning agent reads before future plans."\n'
    )
    parts.append("---")
    parts.append(yaml_block.rstrip())
    parts.append("---")
    parts.append("")
    parts.append("[//]: # (auto-generated by scripts/generate-lessons-page.py; source: planner_lessons)")
    parts.append("")

    # Intro
    parts.append("# AI Greenhouse Lessons Learned")
    parts.append("")
    parts.append(
        "A curated operations playbook distilled from Verdify's planner lesson table. "
        "The public page leads with durable greenhouse knowledge; noisy machine extraction rows stay out of the reading path."
    )
    parts.append("")
    parts.append('<div class="data-table">')
    parts.append(
        '  <div class="data-row"><strong>Related evidence</strong><span><a href="/data/planning-quality/">Scorecard</a> · <a href="/reference/planning-loop/">Planning Loop</a> · <a href="/reference/ai-tunables/">AI Tunables Traceability</a></span><p>This generated page owns durable lesson wording. Scorecards, plan mechanics, and per-parameter evidence stay on their canonical pages.</p></div>'
    )
    parts.append("</div>")
    parts.append("")

    parts.append("## Operating Priorities")
    parts.append("")
    parts.append("- Gas heat is 3.9x cheaper per BTU than electric for sustained cold.")
    parts.append("- Direct relay control stays in firmware.")
    parts.append("- Shade cloth, not software, is the missing hot-day fix.")
    parts.append("- VPD is the dominant spring constraint.")
    parts.append("- Stale plans must be replaced when forecast regime changes.")
    parts.append("")
    parts.append(
        "Those lessons constrain the bounded control surface listed in "
        "[AI Tunables Traceability](/reference/ai-tunables/)."
    )
    parts.append("")
    parts.append(
        f"Generated {today} from {len(active)} active rows and {len(superseded)} retired rows in planner_lessons."
    )
    parts.append("")

    if active:
        parts.extend(render_operational_playbook(active))
        parts.extend(render_current_register(active_groups))
    else:
        parts.append("*No active lessons yet.*")
        parts.append("")

    parts.extend(render_audit_boundary(all_groups, all_lessons))

    # Lesson Lifecycle
    parts.append("## Lesson Lifecycle")
    parts.append("")
    parts.append("1. **Hypothesis** — Planner proposes a theory during a planning cycle")
    parts.append("2. **Test** — Specific setpoint changes are made with measurable expected outcomes")
    parts.append("3. **Validate** — Next cycle scores the result (1–10) and extracts findings")
    parts.append('4. **Graduate** — If finding is significant, it\'s added to this page at confidence "low"')
    parts.append("5. **Confirm** — Each re-validation bumps confidence (low → medium at 3×, high at 5×)")
    parts.append("6. **Supersede** — If a better approach is found, old lesson is marked superseded")
    parts.append("")

    return "\n".join(parts)


def generate_raw_page() -> str:
    """Generate a noindex row-level audit page for planner_lessons."""
    _active, _superseded, _active_groups, all_lessons, all_groups = build_lesson_sets()
    today = date.today().isoformat()
    parts = [
        "---",
        'title: "Raw Planner Lesson Rows"',
        'description: "Noindex row-level audit trail for Verdify planner_lessons."',
        "tags: [greenhouse, planning, lessons, raw-audit]",
        "date: " + today,
        "noindex: true",
        "---",
        "",
        "[//]: # (auto-generated by scripts/generate-lessons-page.py; source: planner_lessons)",
        "",
        "# Raw Planner Lesson Rows",
        "",
        "This page exists for auditability, not as the public reading path. "
        "[Start with the curated Lessons page](/reference/lessons/).",
        "",
        f"Generated {today}: {len(all_lessons)} raw rows collapsed into {len(all_groups)} grouped signals.",
        "",
        "| Row | State | Confidence | Grouped signal | Summary |",
        "|---|---|---|---|---|",
    ]
    for lesson in sorted(all_lessons, key=lambda row: row["id"]):
        state = "active" if lesson.get("is_active", False) else "retired"
        confidence = f"{lesson['confidence'].capitalize()}, {lesson['times_validated']}x"
        summary = operational_summary(lesson)
        if lesson["id"] == 50:
            summary = TOPIC_SUMMARIES["mild_misting_reboot_split"]
        if lesson.get("superseded_by"):
            summary = f"{summary} Superseded by lesson {lesson['superseded_by']}."
        parts.append(f"| L{lesson['id']} | {state} | {confidence} | {topic_label(lesson)} | {summary} |")
    parts.append("")
    return "\n".join(parts)


def main():
    content = generate_page()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(content)
    print(f"Lessons page written to {OUTPUT_PATH} ({len(content)} bytes)")
    raw_content = generate_raw_page()
    os.makedirs(os.path.dirname(RAW_OUTPUT_PATH), exist_ok=True)
    with open(RAW_OUTPUT_PATH, "w") as f:
        f.write(raw_content)
    print(f"Raw lessons page written to {RAW_OUTPUT_PATH} ({len(raw_content)} bytes)")


if __name__ == "__main__":
    main()
