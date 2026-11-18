"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  GatewayError,
  listMissions,
  type AcademyMissionSummary,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";

export function MissionsGallery() {
  const [missions, setMissions] = useState<AcademyMissionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await listMissions();
        if (!cancelled) setMissions(body.missions);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof GatewayError
            ? `${err.message} [${err.code}]`
            : err instanceof Error
              ? err.message
              : "Failed to load missions",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="academy-layout academy-layout-lively">
      <AcademySubnav />
      <header className="academy-hero academy-hero-lively">
        <div className="academy-hero-copy">
          <p className="empty-kicker academy-hero-kicker">Missions</p>
          <h1>Workflow studio</h1>
          <p className="academy-hero-lead">
            Multi-step Gateway-mediated practice: Recipes → RAG → structured
            output. Honesty constraints are authored — never invent customers,
            serials, or readiness.
          </p>
          <div className="academy-hero-actions">
            <Link className="btn" href="/academy/missions/siteops-incident-loop">
              Recommended — Site Ops live desk
            </Link>
          </div>
        </div>
      </header>
      {error ? <div className="message-error models-error">{error}</div> : null}
      {!missions.length && !error ? (
        <p className="muted academy-skeleton">Loading missions…</p>
      ) : (
        <section className="academy-group academy-group-ember">
          <header className="academy-group-head">
            <div>
              <h2>Role workflows</h2>
              <p>Open a mission, mark steps, submit evidence — progress only.</p>
            </div>
          </header>
          <div className="academy-tracks academy-tracks-grid">
            {missions.map((mission) => {
              const status = mission.progress?.status ?? "not_started";
              const done = mission.progress?.completed_steps?.length ?? 0;
              const total = mission.step_count || 1;
              const percent = Math.min(100, Math.round((done / total) * 100));
              return (
                <article
                  key={mission.id}
                  className={`academy-track academy-track-ember academy-track-${
                    status === "completed" ? "complete" : done ? "progress" : "empty"
                  }`}
                >
                  <div className="academy-track-head">
                    <div>
                      <p className="lab-card-kicker">{mission.track}</p>
                      <h3>{mission.title}</h3>
                      <p className="academy-track-summary">{mission.summary}</p>
                      <p className="muted">
                        {done}/{total} steps · {mission.duration_minutes} min
                      </p>
                    </div>
                    <div
                      className="academy-progress"
                      aria-label={`${percent}% steps marked`}
                    >
                      <div className="academy-progress-track">
                        <div
                          className="academy-progress-bar"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                      <span>{percent}%</span>
                    </div>
                  </div>
                  <span className={`academy-status academy-status-${status}`}>
                    {status.replace("_", " ")}
                  </span>
                  <Link className="btn academy-continue" href={`/academy/missions/${mission.id}`}>
                    Open mission
                  </Link>
                </article>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
