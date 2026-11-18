"""Academy missions + governed prompt library loaders."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.paths import resolve_repo_root

MissionStepKind = Literal["recipe", "sandbox", "lab_ability", "lesson", "check", "playground"]
PromptStatus = Literal["reviewed", "draft"]


class MissionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    kind: MissionStepKind
    instruction: str
    href: str | None = None
    recipe_id: str | None = None
    ability: str | None = None
    sandbox_preset: str | None = None


class MissionCompletionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["eval_case"] = "eval_case"
    prompt: str
    expect_substrings: list[str] = Field(min_length=1)
    any_of_groups: list[list[str]] = Field(default_factory=list)
    forbid_substrings: list[str] = Field(default_factory=list)
    case_sensitive: bool = False


class Mission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    track: str
    summary: str
    duration_minutes: int = Field(ge=1, le=180)
    honesty: list[str] = Field(default_factory=list)
    steps: list[MissionStep] = Field(min_length=1)
    completion_check: MissionCompletionCheck


class PromptPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    track: str
    title: str
    status: PromptStatus = "reviewed"
    system: str
    user_template: str
    links: dict[str, str] = Field(default_factory=dict)


class PromptLibrary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    patterns: list[PromptPattern] = Field(min_length=1)


def _lab_root() -> Path:
    return resolve_repo_root() / "config" / "lab"


def load_missions(lab_root: Path | None = None) -> dict[str, Mission]:
    root = (lab_root or _lab_root()) / "missions"
    missions: dict[str, Mission] = {}
    if not root.is_dir():
        return missions
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        mission = Mission.model_validate(data)
        if path.stem != mission.id:
            raise ValueError(f"mission file {path.name} stem must match id {mission.id}")
        if mission.id in missions:
            raise ValueError(f"duplicate mission id {mission.id}")
        missions[mission.id] = mission
    return missions


def load_prompt_library(lab_root: Path | None = None) -> PromptLibrary:
    path = (lab_root or _lab_root()) / "prompts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PromptLibrary.model_validate(data)


@lru_cache(maxsize=1)
def get_missions() -> dict[str, Mission]:
    return load_missions()


@lru_cache(maxsize=1)
def get_prompt_library() -> PromptLibrary:
    return load_prompt_library()


def reload_missions_and_prompts() -> None:
    get_missions.cache_clear()
    get_prompt_library.cache_clear()


def missions_public_list(missions: dict[str, Mission]) -> list[dict[str, Any]]:
    return [
        {
            "id": m.id,
            "title": m.title,
            "track": m.track,
            "summary": m.summary,
            "duration_minutes": m.duration_minutes,
            "honesty": m.honesty,
            "step_count": len(m.steps),
        }
        for m in sorted(missions.values(), key=lambda item: item.id)
    ]


def mission_public_dict(mission: Mission) -> dict[str, Any]:
    return {
        "id": mission.id,
        "title": mission.title,
        "track": mission.track,
        "summary": mission.summary,
        "duration_minutes": mission.duration_minutes,
        "honesty": mission.honesty,
        "steps": [s.model_dump() for s in mission.steps],
        "completion_check": {
            "kind": mission.completion_check.kind,
            "prompt": mission.completion_check.prompt,
        },
    }


def evaluate_mission_check(mission: Mission, answer: str) -> tuple[bool, str]:
    check = mission.completion_check
    hay = answer if check.case_sensitive else answer.lower()

    def has(needle: str) -> bool:
        target = needle if check.case_sensitive else needle.lower()
        return target in hay

    missing = [n for n in check.expect_substrings if not has(n)]
    if missing:
        return False, f"Missing expected evidence: {', '.join(missing)}"
    for group in check.any_of_groups:
        if not any(has(n) for n in group):
            return False, f"Missing any-of group evidence: {', '.join(group)}"
    forbidden_hit = [n for n in check.forbid_substrings if has(n)]
    if forbidden_hit:
        return False, f"Forbidden evidence present: {', '.join(forbidden_hit)}"
    return True, "Mission check passed."
