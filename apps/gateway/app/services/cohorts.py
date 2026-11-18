"""Cohorts, challenges, and honest completion rubrics (no invented scores)."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.paths import resolve_repo_root


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ChallengeCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class ChallengeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "eval_case"
    prompt: str
    expect_substrings: list[str] = Field(default_factory=list)
    any_of_groups: list[list[str]] = Field(default_factory=list)
    forbid_substrings: list[str] = Field(default_factory=list)
    case_sensitive: bool = False


class Challenge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str
    track_hint: str = ""
    sandbox_preset: str | None = None
    mission_id: str | None = None
    criteria: list[ChallengeCriterion] = Field(default_factory=list)
    completion_check: ChallengeCheck


class Cohort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str
    starts_at: str
    ends_at: str
    challenge_ids: list[str] = Field(min_length=1)


class CohortCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    cohorts: list[Cohort]
    challenges: list[Challenge]


class ChallengeSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    principal_subject: str
    answer: str
    criteria_met: list[str]
    criteria_missed: list[str]
    passed: bool
    submitted_at: str


def load_cohort_catalog(lab_root: Path | None = None) -> CohortCatalog:
    path = (lab_root or (resolve_repo_root() / "config" / "lab")) / "cohorts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CohortCatalog.model_validate(data)


@lru_cache(maxsize=1)
def get_cohort_catalog() -> CohortCatalog:
    return load_cohort_catalog()


def evaluate_challenge(challenge: Challenge, answer: str) -> tuple[bool, list[str], list[str], str]:
    """Return passed, met ids, missed ids, explanation. Never invent scores."""

    check = challenge.completion_check
    hay = answer if check.case_sensitive else answer.lower()
    met: list[str] = []
    missed: list[str] = []

    def has(needle: str) -> bool:
        target = needle if check.case_sensitive else needle.lower()
        return target in hay

    required_ok = all(has(n) for n in check.expect_substrings)
    groups_ok = [any(has(n) for n in group) for group in check.any_of_groups]
    forbidden_hit = [n for n in check.forbid_substrings if has(n)]

    # Assign criteria in order: required block, then each any_of group, then forbid rule.
    crits = list(challenge.criteria)
    cursor = 0
    if crits:
        if required_ok:
            met.append(crits[cursor].id)
        else:
            missed.append(crits[cursor].id)
        cursor += 1
    for group_ok in groups_ok:
        if cursor >= len(crits):
            break
        if group_ok:
            met.append(crits[cursor].id)
        else:
            missed.append(crits[cursor].id)
        cursor += 1
    if check.forbid_substrings and cursor < len(crits):
        if forbidden_hit:
            missed.append(crits[cursor].id)
        else:
            met.append(crits[cursor].id)

    if forbidden_hit:
        return (
            False,
            met,
            missed,
            f"Forbidden evidence present: {', '.join(forbidden_hit)}",
        )

    passed = (
        required_ok
        and all(groups_ok)
        and not forbidden_hit
        and (not crits or all(c.id in met for c in crits))
    )
    explanation = (
        "All criteria met."
        if passed
        else f"Criteria missed: {', '.join(missed) or 'requirements'}"
    )
    return passed, met, missed, explanation


class InMemoryChallengeStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ChallengeSubmission] = {}

    def submit(self, submission: ChallengeSubmission) -> ChallengeSubmission:
        self._rows[(submission.principal_subject, submission.challenge_id)] = submission
        return submission

    def get(self, principal_subject: str, challenge_id: str) -> ChallengeSubmission | None:
        return self._rows.get((principal_subject, challenge_id))

    def list_for(self, principal_subject: str) -> list[ChallengeSubmission]:
        return [s for (subj, _), s in self._rows.items() if subj == principal_subject]

    def list_all(self) -> list[ChallengeSubmission]:
        return list(self._rows.values())

    def leaderboard(self, challenge_id: str) -> list[dict[str, Any]]:
        """Honest board: who met criteria — never numeric vanity scores."""

        rows = [s for s in self._rows.values() if s.challenge_id == challenge_id and s.passed]
        rows.sort(key=lambda s: s.submitted_at)
        return [
            {
                "principal_subject": s.principal_subject,
                "passed": True,
                "criteria_met": s.criteria_met,
                "submitted_at": s.submitted_at,
            }
            for s in rows
        ]


def catalog_public(catalog: CohortCatalog) -> dict[str, Any]:
    challenges = {c.id: c for c in catalog.challenges}
    return {
        "version": catalog.version,
        "cohorts": [
            {
                **cohort.model_dump(),
                "challenges": [
                    {
                        "id": challenges[cid].id,
                        "title": challenges[cid].title,
                        "summary": challenges[cid].summary,
                        "track_hint": challenges[cid].track_hint,
                        "sandbox_preset": challenges[cid].sandbox_preset,
                        "mission_id": challenges[cid].mission_id,
                        "criteria": [c.model_dump() for c in challenges[cid].criteria],
                        "completion_prompt": challenges[cid].completion_check.prompt,
                    }
                    for cid in cohort.challenge_ids
                    if cid in challenges
                ],
            }
            for cohort in catalog.cohorts
        ],
    }
