"""Instructor enablement helpers — capability-gated, no local IAM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.curriculum import Curriculum
from app.services.progress import LessonProgress, progress_overlay, track_completion_stats


class CorpusQuarantineStore:
    """Flags teaching corpora/presets as unavailable for Practice desks."""

    def __init__(self) -> None:
        self._quarantined: set[str] = set()

    def list(self) -> list[str]:
        return sorted(self._quarantined)

    def quarantine(self, corpus_id: str) -> None:
        self._quarantined.add(corpus_id)

    def release(self, corpus_id: str) -> None:
        self._quarantined.discard(corpus_id)

    def is_quarantined(self, corpus_id: str) -> bool:
        return corpus_id in self._quarantined


def aggregate_track_completion(
    *,
    curriculum: Curriculum,
    all_progress: list[LessonProgress],
    stall_days: int = 7,
) -> dict[str, Any]:
    """Aggregate completion counts by track. Never invent per-person scores."""

    subjects = sorted({p.principal_subject for p in all_progress})
    by_subject: dict[str, list[LessonProgress]] = {s: [] for s in subjects}
    for row in all_progress:
        by_subject[row.principal_subject].append(row)

    now = datetime.now(UTC)
    stall_cutoff = now - timedelta(days=max(1, stall_days))

    tracks_out = []
    for track in curriculum.tracks:
        completed_learners = 0
        in_progress_learners = 0
        for _subject, rows in by_subject.items():
            overlay = progress_overlay(lesson_ids=track.lesson_ids, rows=rows)
            stats = track_completion_stats(track.lesson_ids, overlay)
            if stats["completed"] == stats["total"] and stats["total"] > 0:
                completed_learners += 1
            elif stats["completed"] > 0 or any(
                overlay.get(lid, {}).get("status") == "in_progress"
                for lid in track.lesson_ids
            ):
                in_progress_learners += 1

        lessons_detail = []
        for lesson_id in track.lesson_ids:
            completed_count = 0
            in_progress_count = 0
            stalled_count = 0
            for row in all_progress:
                if row.lesson_id != lesson_id:
                    continue
                if row.status == "completed":
                    completed_count += 1
                elif row.status == "in_progress":
                    in_progress_count += 1
                    updated = row.updated_at
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=UTC)
                    if updated < stall_cutoff:
                        stalled_count += 1
            lessons_detail.append(
                {
                    "lesson_id": lesson_id,
                    "completed_count": completed_count,
                    "in_progress_count": in_progress_count,
                    "stalled_count": stalled_count,
                }
            )

        tracks_out.append(
            {
                "track_id": track.id,
                "title": track.title,
                "learners_completed": completed_learners,
                "learners_in_progress": in_progress_learners,
                "learners_seen": len(subjects),
                "lesson_total": len(track.lesson_ids),
                "lessons": lessons_detail,
            }
        )
    return {
        "principals_seen": len(subjects),
        "tracks": tracks_out,
        "stall_days": stall_days,
        "class_runner": {
            "recommended_mission_id": "siteops-incident-loop",
            "recommended_cohort_id": "lab-wave-aug2026",
            "join_urls": {
                "mission": "/academy/missions/siteops-incident-loop",
                "cohorts": "/academy/cohorts",
                "practice": "/academy/sandbox?preset=site-ops-fixture",
            },
            "note": (
                "Class runner v0: share join URLs with learners. Progress is "
                "principal-scoped; no vanity scores."
            ),
        },
        "note": (
            "Counts are from stored progress only. Missing principals are not "
            "invented. Corporate auth cutover will supply richer group filters later."
        ),
    }
