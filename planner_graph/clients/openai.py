"""LLM client for diagnosis and proposal drafting.

This module is the planner's model-facing adapter for the OpenAI Responses API
and the deterministic fallback path. It connects prompt construction and bounded
planner context to structured diagnosis and proposal outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast
from urllib import error, request

from planner_graph.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    PROPOSAL_SYSTEM_PROMPT,
    planner_user_prompt,
)


class PlannerLLMError(RuntimeError):
    pass


@dataclass
class OpenAIPlannerClient:
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5.5"
    reasoning_effort: str = "medium"
    timeout_seconds: float = 30.0

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def diagnose(self, state: dict[str, object]) -> dict[str, object]:
        if not self.is_configured:
            return self._fallback_diagnose(state)
        schema = {
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "likely_cause": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "planning_intent": {"type": "string"},
            },
            "required": ["situation", "likely_cause", "risks", "planning_intent"],
            "additionalProperties": False,
        }
        return cast(
            dict[str, object],
            self._responses_create(
                schema_name="planner_diagnosis",
                schema=schema,
                instructions=DIAGNOSIS_SYSTEM_PROMPT,
                prompt=planner_user_prompt(state, include_diagnosis=False),
            ),
        )

    def draft_plan(self, state: dict[str, object]) -> dict[str, object]:
        if not self.is_configured:
            return self._fallback_draft_plan(state)
        schema = {
            "type": "object",
            "properties": {
                "selected_action": {
                    "type": "string",
                    "enum": ["set_plan", "set_tunable", "acknowledge_trigger", "fail"],
                },
                "rationale": {"type": "string"},
                "confidence": {"type": "number"},
                "tunable_changes": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                },
                "expected_effect": {"type": "string"},
            },
            "required": [
                "selected_action",
                "rationale",
                "confidence",
                "tunable_changes",
                "expected_effect",
            ],
            "additionalProperties": False,
        }
        return cast(
            dict[str, object],
            self._responses_create(
                schema_name="planner_draft",
                schema=schema,
                instructions=PROPOSAL_SYSTEM_PROMPT,
                prompt=planner_user_prompt(state, include_diagnosis=True),
            ),
        )

    def _responses_create(
        self,
        *,
        schema_name: str,
        schema: dict[str, object],
        instructions: str,
        prompt: str,
    ) -> dict[str, object]:
        if self.api_key is None:
            raise PlannerLLMError("OpenAI API key is not configured.")
        payload = {
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": instructions}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        response = self._post_responses(payload)
        parsed = self._extract_output_json(response)
        if not isinstance(parsed, dict):
            raise PlannerLLMError("Structured planner response was not a JSON object.")
        return cast(dict[str, object], parsed)

    def _post_responses(self, payload: dict[str, object]) -> dict[str, object]:
        if self.api_key is None:
            raise PlannerLLMError("OpenAI API key is not configured.")
        raw_request = request.Request(
            url=f"{self.base_url.rstrip('/')}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(
                raw_request, timeout=self.timeout_seconds
            ) as http_response:
                return cast(
                    dict[str, object], json.loads(http_response.read().decode("utf-8"))
                )
        except error.HTTPError as http_error:
            details = http_error.read().decode("utf-8", errors="replace")
            raise PlannerLLMError(
                f"OpenAI Responses API request failed: {details}"
            ) from http_error
        except error.URLError as url_error:
            raise PlannerLLMError(
                f"OpenAI Responses API request failed: {url_error.reason}"
            ) from url_error

    def _extract_output_json(self, response: dict[str, object]) -> dict[str, object]:
        refusal = response.get("refusal")
        if isinstance(refusal, str) and refusal:
            raise PlannerLLMError(f"Model refusal: {refusal}")

        output_parsed = response.get("output_parsed")
        if isinstance(output_parsed, dict):
            return cast(dict[str, object], output_parsed)

        output_text = response.get("output_text")
        if isinstance(output_text, str) and output_text:
            return cast(dict[str, object], json.loads(output_text))

        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("refusal"), str) and item.get("refusal"):
                    raise PlannerLLMError(f"Model refusal: {item['refusal']}")
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if isinstance(part.get("refusal"), str) and part.get("refusal"):
                        raise PlannerLLMError(f"Model refusal: {part['refusal']}")
                    if part.get("type") == "output_json" and isinstance(
                        part.get("json"), dict
                    ):
                        return cast(dict[str, object], part["json"])
                    if part.get("type") in {"output_text", "text"}:
                        text_value = part.get("text")
                        if isinstance(text_value, str) and text_value:
                            return cast(dict[str, object], json.loads(text_value))
                        if isinstance(text_value, dict):
                            value = text_value.get("value")
                            if isinstance(value, str) and value:
                                return cast(dict[str, object], json.loads(value))

        raise PlannerLLMError(
            "OpenAI response did not contain structured planner output."
        )

    def _fallback_diagnose(self, state: dict[str, object]) -> dict[str, object]:
        scorecard = cast(dict[str, Any], state.get("scorecard_summary", {}))
        climate = cast(dict[str, Any], state.get("climate_snapshot", {}))
        event_type = state.get("event_type", "UNKNOWN")
        return {
            "situation": (
                f"{event_type} trigger with planner score "
                f"{scorecard.get('planner_score')} and VPD {climate.get('vpd_kpa')}"
            ),
            "likely_cause": "Fallback planner path used because OpenAI credentials are not configured.",
            "risks": [
                "Verdify MCP remains the only write boundary.",
                "Planner quality is degraded without the configured LLM path.",
            ],
            "planning_intent": "Exercise the end-to-end planner safely until the structured LLM path is configured.",
        }

    def _fallback_draft_plan(self, state: dict[str, object]) -> dict[str, object]:
        event_type = str(state.get("event_type", "UNKNOWN"))
        alerts = state.get("alerts_summary", [])
        forecast = cast(dict[str, Any], state.get("forecast_summary", {}))
        selected_action = "acknowledge_trigger"
        tunable_changes: dict[str, float] = {}
        if event_type in {"SUNRISE", "SUNSET", "MIDNIGHT"}:
            selected_action = "set_plan"
        elif isinstance(alerts, list) and any(
            "critical" in str(item).lower() for item in alerts
        ):
            selected_action = "set_tunable"
            tunable_changes = {"fog_escalation_kpa": 0.4}
        return {
            "selected_action": selected_action,
            "rationale": (
                f"Fallback planner chose {selected_action} for {event_type}. "
                f"Forecast summary: {forecast.get('headline')}"
            ),
            "confidence": 0.35,
            "tunable_changes": tunable_changes,
            "expected_effect": "Bounded production payload generated by the deterministic fallback planner.",
        }
