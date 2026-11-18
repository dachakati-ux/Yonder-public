"""Academy curriculum loader — YAML tracks + lessons under config/lab/."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.paths import resolve_repo_root

TrackId = Literal[
    "foundations",
    "site_ops",
    "operator",
    "builder",
    "trust",
    "support",
    "security",
    "pm",
    "managers",
    "capacity",
]
TryItKind = Literal[
    "lab_ability", "playground", "recipe", "chat_starter", "sandbox", "mission"
]
CheckKind = Literal["quiz", "eval_case"]
CompletionRule = Literal["check_passed", "try_it_recorded", "check_and_try_it"]


class QuizOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class QuizCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quiz"] = "quiz"
    prompt: str
    options: list[QuizOption] = Field(min_length=2)
    correct_option_id: str
    explanation: str = ""


class EvalCaseCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["eval_case"] = "eval_case"
    prompt: str
    expect_substrings: list[str] = Field(min_length=1)
    any_of_groups: list[list[str]] = Field(default_factory=list)
    forbid_substrings: list[str] = Field(default_factory=list)
    case_sensitive: bool = False


class TryIt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TryItKind
    label: str
    href: str | None = None
    ability: str | None = None
    recipe_id: str | None = None
    chat_prompt: str | None = None
    sandbox_preset: str | None = None


class LessonSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body: str


class Lesson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    track: TrackId
    title: str
    summary: str
    duration_minutes: int = Field(ge=1, le=120)
    objectives: list[str] = Field(min_length=1)
    sections: list[LessonSection] = Field(min_length=1)
    glossary_term_ids: list[str] = Field(default_factory=list)
    try_it: TryIt | None = None
    check: QuizCheck | EvalCaseCheck
    completion: CompletionRule = "check_passed"
    prerequisites: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not value or " " in value:
            raise ValueError("lesson id must be non-empty without spaces")
        return value


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: TrackId
    title: str
    summary: str
    audience: str = "everyone"
    lesson_ids: list[str] = Field(min_length=1)


class SandboxQuotas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_sandboxes_per_principal: int = 3
    max_documents_per_sandbox: int = 8
    max_chars_per_document: int = 8000
    max_chars_per_sandbox: int = 40000
    max_retrieve_runs_per_sandbox: int = 40
    max_parts_entries_per_sandbox: int = 50


class Curriculum(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    tracks: list[Track] = Field(min_length=1)
    sandbox: SandboxQuotas = Field(default_factory=SandboxQuotas)
    lessons: dict[str, Lesson] = Field(default_factory=dict)

    def lesson(self, lesson_id: str) -> Lesson | None:
        return self.lessons.get(lesson_id)

    def next_lesson_id(self, lesson_id: str) -> str | None:
        for track in self.tracks:
            if lesson_id not in track.lesson_ids:
                continue
            idx = track.lesson_ids.index(lesson_id)
            if idx + 1 < len(track.lesson_ids):
                return track.lesson_ids[idx + 1]
            return None
        return None

    def validate_integrity(self) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        for track in self.tracks:
            for lid in track.lesson_ids:
                if lid in seen:
                    errors.append(f"duplicate lesson id in curriculum: {lid}")
                seen.add(lid)
                lesson = self.lessons.get(lid)
                if lesson is None:
                    errors.append(f"track {track.id} references missing lesson {lid}")
                    continue
                if lesson.track != track.id:
                    errors.append(
                        f"lesson {lid} track={lesson.track} does not match "
                        f"curriculum track {track.id}"
                    )
                if lesson.check.kind == "quiz":
                    option_ids = {opt.id for opt in lesson.check.options}
                    if lesson.check.correct_option_id not in option_ids:
                        errors.append(f"lesson {lid}: correct_option_id not in options")
                for pre in lesson.prerequisites:
                    if pre not in self.lessons and pre not in seen:
                        # may appear later in same load — check after loop
                        pass
        for lesson in self.lessons.values():
            for pre in lesson.prerequisites:
                if pre not in self.lessons:
                    errors.append(f"lesson {lesson.id}: unknown prerequisite {pre}")
        orphan = set(self.lessons) - seen
        for lid in sorted(orphan):
            errors.append(f"lesson {lid} not listed in any track")
        return errors


def _lab_root() -> Path:
    return resolve_repo_root() / "config" / "lab"


def load_curriculum(lab_root: Path | None = None) -> Curriculum:
    root = lab_root or _lab_root()
    curriculum_path = root / "curriculum.yaml"
    raw = yaml.safe_load(curriculum_path.read_text(encoding="utf-8")) or {}
    lessons_dir = root / "lessons"
    lessons: dict[str, Lesson] = {}
    if lessons_dir.is_dir():
        for path in sorted(lessons_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            lesson = Lesson.model_validate(data)
            if lesson.id in lessons:
                raise ValueError(f"duplicate lesson file for id {lesson.id}")
            if path.stem != lesson.id:
                raise ValueError(
                    f"lesson file {path.name} stem must match id {lesson.id}"
                )
            lessons[lesson.id] = lesson
    curriculum = Curriculum.model_validate({**raw, "lessons": lessons})
    integrity = curriculum.validate_integrity()
    if integrity:
        raise ValueError("curriculum integrity errors:\n- " + "\n- ".join(integrity))
    return curriculum


@lru_cache(maxsize=1)
def get_curriculum() -> Curriculum:
    return load_curriculum()


def reload_curriculum() -> Curriculum:
    get_curriculum.cache_clear()
    return get_curriculum()


def lesson_public_dict(curriculum: Curriculum, lesson: Lesson) -> dict[str, Any]:
    """Serialize one lesson without quiz answers."""

    if lesson.check.kind == "quiz":
        check_public: dict[str, Any] = {
            "kind": "quiz",
            "prompt": lesson.check.prompt,
            "options": [opt.model_dump() for opt in lesson.check.options],
        }
    else:
        check_public = {
            "kind": "eval_case",
            "prompt": lesson.check.prompt,
        }
    return {
        "id": lesson.id,
        "track": lesson.track,
        "title": lesson.title,
        "summary": lesson.summary,
        "duration_minutes": lesson.duration_minutes,
        "objectives": lesson.objectives,
        "sections": [s.model_dump() for s in lesson.sections],
        "glossary_term_ids": lesson.glossary_term_ids,
        "try_it": lesson.try_it.model_dump() if lesson.try_it else None,
        "check": check_public,
        "completion": lesson.completion,
        "prerequisites": lesson.prerequisites,
        "next_lesson_id": curriculum.next_lesson_id(lesson.id),
    }


def curriculum_public_dict(curriculum: Curriculum) -> dict[str, Any]:
    """Serialize curriculum without quiz answers."""

    tracks = []
    for track in curriculum.tracks:
        lesson_cards = [
            lesson_public_dict(curriculum, curriculum.lessons[lid])
            for lid in track.lesson_ids
        ]
        tracks.append(
            {
                "id": track.id,
                "title": track.title,
                "summary": track.summary,
                "audience": track.audience,
                "lesson_ids": track.lesson_ids,
                "lessons": lesson_cards,
            }
        )
    return {
        "version": curriculum.version,
        "tracks": tracks,
        "sandbox": curriculum.sandbox.model_dump(),
    }
