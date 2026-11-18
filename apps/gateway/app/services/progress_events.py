"""Apply Academy progress events (start / check / try_it / complete)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.curriculum import Curriculum, Lesson
from app.services.progress import (
    LessonProgress,
    ProgressEventResult,
    ProgressStore,
    empty_progress,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _maybe_complete(lesson: Lesson, progress: LessonProgress) -> bool:
    if lesson.completion == "check_passed":
        return progress.check_passed
    if lesson.completion == "try_it_recorded":
        return progress.try_it_recorded
    return progress.check_passed and progress.try_it_recorded


def evaluate_check(lesson: Lesson, payload: dict[str, Any]) -> tuple[bool, str]:
    check = lesson.check
    if check.kind == "quiz":
        selected = str(payload.get("selected_option_id") or "").strip()
        correct = selected == check.correct_option_id
        explanation = check.explanation or (
            "Correct." if correct else "Incorrect — review the lesson sections."
        )
        return correct, explanation
    # eval_case
    answer = str(payload.get("answer") or "")
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
    return True, "All expected substrings present."


async def apply_progress_event(
    *,
    store: ProgressStore,
    curriculum: Curriculum,
    principal_subject: str,
    lesson_id: str,
    event: str,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> ProgressEventResult:
    lesson = curriculum.lesson(lesson_id)
    if lesson is None:
        raise KeyError("lesson_not_found")

    progress = await store.get_progress(principal_subject, lesson_id)
    if progress is None:
        progress = empty_progress(principal_subject, lesson_id)

    payload = payload or {}
    check_correct: bool | None = None
    explanation: str | None = None
    now = _now()

    if event == "start":
        if progress.status == "not_started":
            progress = progress.model_copy(
                update={"status": "in_progress", "updated_at": now}
            )
    elif event == "try_it":
        progress = progress.model_copy(
            update={
                "status": "completed"
                if progress.status == "completed"
                else "in_progress",
                "try_it_recorded": True,
                "last_try_it_request_id": request_id
                or payload.get("request_id")
                or progress.last_try_it_request_id,
                "updated_at": now,
            }
        )
    elif event == "check":
        check_correct, explanation = evaluate_check(lesson, payload)
        progress = progress.model_copy(
            update={
                "status": "in_progress"
                if progress.status != "completed"
                else progress.status,
                "check_attempts": progress.check_attempts + 1,
                "check_passed": progress.check_passed or check_correct,
                "last_check_correct": check_correct,
                "updated_at": now,
            }
        )
    elif event == "complete":
        # Explicit complete only succeeds when completion rule is satisfied.
        pass
    else:
        raise ValueError(f"unknown_event:{event}")

    completed = _maybe_complete(lesson, progress)
    if event == "complete" and not completed:
        raise ValueError("completion_requirements_not_met")
    if completed and progress.status != "completed":
        progress = progress.model_copy(
            update={
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
            }
        )

    saved = await store.upsert_progress(progress)
    return ProgressEventResult(
        lesson_id=lesson_id,
        status=saved.status,
        check_passed=saved.check_passed,
        try_it_recorded=saved.try_it_recorded,
        check_correct=check_correct,
        explanation=explanation,
        completed=saved.status == "completed",
        progress=saved,
    )
