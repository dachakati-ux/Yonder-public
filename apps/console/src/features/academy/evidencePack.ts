/** Compose a principal-scoped evidence pack (client-side; no invented ranks). */

import {
  exportWorkbench,
  getAcademyCurriculum,
  getCapacitySnapshot,
  listCohorts,
  listMissions,
  getMission,
} from "@/lib/gateway";

export type EvidencePack = {
  exported_at: string;
  note: string;
  curriculum: {
    lessons_completed: string[];
    lessons_in_progress: string[];
    tracks: Array<{ id: string; completed: number; total: number }>;
  };
  missions: Array<{
    id: string;
    status: string;
    completed_steps: string[];
    check_passed: boolean;
  }>;
  challenges: Array<{
    id: string;
    passed: boolean | null;
    criteria_met: string[];
    criteria_missed: string[];
  }>;
  workbench: Awaited<ReturnType<typeof exportWorkbench>>;
  capacity: Awaited<ReturnType<typeof getCapacitySnapshot>>;
};

export async function buildEvidencePack(): Promise<EvidencePack> {
  const [curriculum, missionsList, cohorts, workbench, capacity] =
    await Promise.all([
      getAcademyCurriculum(),
      listMissions(),
      listCohorts(),
      exportWorkbench(),
      getCapacitySnapshot(),
    ]);

  const lessons_completed: string[] = [];
  const lessons_in_progress: string[] = [];
  for (const track of curriculum.tracks) {
    for (const lesson of track.lessons) {
      const status = lesson.progress?.status ?? "not_started";
      if (status === "completed") lessons_completed.push(lesson.id);
      if (status === "in_progress") lessons_in_progress.push(lesson.id);
    }
  }

  const missions = await Promise.all(
    missionsList.missions.map(async (summary) => {
      try {
        const detail = await getMission(summary.id);
        return {
          id: summary.id,
          status: detail.progress?.status ?? "not_started",
          completed_steps: detail.progress?.completed_steps ?? [],
          check_passed: detail.progress?.check_passed ?? false,
        };
      } catch {
        return {
          id: summary.id,
          status: summary.progress?.status ?? "not_reported",
          completed_steps: summary.progress?.completed_steps ?? [],
          check_passed: summary.progress?.check_passed ?? false,
        };
      }
    }),
  );

  const challenges = cohorts.cohorts.flatMap((cohort) =>
    cohort.challenges.map((challenge) => ({
      id: challenge.id,
      passed: challenge.submission?.passed ?? null,
      criteria_met: challenge.submission?.criteria_met ?? [],
      criteria_missed: challenge.submission?.criteria_missed ?? [],
    })),
  );

  return {
    exported_at: new Date().toISOString(),
    note:
      "Principal-scoped evidence only. No ranks or vanity scores. Missing " +
      "fields are omitted or marked not_reported — never invented.",
    curriculum: {
      lessons_completed,
      lessons_in_progress,
      tracks: curriculum.tracks.map((t) => ({
        id: t.id,
        completed: t.completion.completed,
        total: t.completion.total,
      })),
    },
    missions,
    challenges,
    workbench,
    capacity,
  };
}

export function downloadEvidencePack(pack: EvidencePack): void {
  const text = JSON.stringify(pack, null, 2);
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `yonder-evidence-pack-${pack.workbench.principal_subject || "export"}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
