"""Graph state and the pydantic schemas the LLM must fill in (planning, argument correction, synthesis).

Tool arguments are carried as a JSON *string* (`args_json`), not as an open `dict`: measured on the real model, an open
object schema made Qwen return `{}` for every step (0 of 2 plans carried the buyer and years) while the JSON-string form
carried them every time (2 of 2). `.args` parses the string back into a dict for the rest of the code.
"""
from __future__ import annotations

import json
from typing import Any, TypedDict

from pydantic import BaseModel, Field, model_validator


def parse_json_object(text: str | None) -> dict[str, Any]:
    """A JSON object from model text; anything else (invalid JSON, a list, ...) becomes {} so the caller can repair it."""
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class _ArgsAsJson(BaseModel):
    """Mixin: accept `args={...}` (convenient in tests and code) and store it as `args_json`; expose `.args`."""

    @model_validator(mode="before")
    @classmethod
    def _args_dict_to_json(cls, data: Any) -> Any:
        if isinstance(data, dict) and "args" in data and "args_json" not in data:
            data = dict(data)
            data["args_json"] = json.dumps(data.pop("args"), default=str)
        return data

    @property
    def args(self) -> dict[str, Any]:
        return parse_json_object(getattr(self, "args_json", "{}"))


# ----------------------------------------------------------------------------- LLM output schemas
class Scope(BaseModel):
    """What the request is about, extracted by the Planner (used by follow-up rules as $scope.buyer / $scope.years)."""
    buyer: str | None = Field(default=None, description="Full buyer name from the request, or null")
    years: list[int] | None = Field(default=None, description="Calendar years covered, e.g. [2022, 2023, 2024], or null")


class PlanStep(_ArgsAsJson):
    tool: str = Field(description="Exact tool name from the catalogue")
    args_json: str = Field(description='The tool arguments as a JSON object string, e.g. '
                           '{"buyer": "Acme Ltd", "years": [2023]}. Include the buyer and the years / date range in EVERY '
                           "step whose tool accepts them; never leave this empty when the request names a buyer or period.")
    reason: str = Field(description="One sentence: why this check, why now")


class Plan(BaseModel):
    objective: str = Field(description="One sentence restating what is being investigated")
    scope: Scope
    steps: list[PlanStep]


class ArgsFix(_ArgsAsJson):
    """The model's answer when a tool call failed: corrected arguments, or give up."""
    give_up: bool = Field(default=False, description="True if the call cannot be fixed")
    args_json: str = Field(default="{}", description="Corrected arguments for the same tool, as a JSON object string")
    explanation: str = Field(default="", description="What was wrong and what changed")


class FindingDraft(BaseModel):
    candidate_id: str | None = Field(default=None, description="Id of the candidate flag this finding is about")
    rule: str = Field(default="", description="Tool name that produced the evidence (needed only without candidate_id)")
    severity: str = Field(description="low, medium or high")
    explanation: str = Field(description="At most 40 words, plain, on what the data shows; no recommendations")
    ocids: list[str] = Field(default_factory=list, description="ocids this finding refers to; only ones from the candidate")


class SynthOutput(BaseModel):
    findings: list[FindingDraft] = Field(description="Most important first")
    summary: str = Field(description="3-5 neutral sentences summarising the investigation and its limits")


# ----------------------------------------------------------------------------- graph state
class AgentState(TypedDict, total=False):
    run_id: str
    query: str
    plan: dict[str, Any]
    scope: dict[str, Any]
    queue: list[dict[str, Any]]          # steps still to run (the Investigator pops the first)
    seen: list[str]                      # step keys already run or queued (no repeats)
    steps_done: int                      # tool calls made so far (the step cap counts these)
    results: list[dict[str, Any]]        # one entry per executed step, with the tool output
    notes: list[str]                     # human-readable record of follow-ups, widenings, corrections, errors
    stopped_reason: str | None
    findings: list[dict[str, Any]]       # accepted by the citation guard
    dropped: list[dict[str, Any]]        # rejected by the citation guard (with reasons)
    flags_created: list[dict[str, Any]]  # what flag_finding stored
    summary: str
    review_status: str                   # "pending_review" until the Day 4 human gate
