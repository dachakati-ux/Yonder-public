"use client";

import { useEffect, useState } from "react";
import { GATEWAY_URL } from "@/lib/brand";

type UpdateStatus = {
  status: "revision_differs" | "up_to_date" | "installed_revision_unknown" | "check_unavailable";
  installed_revision: string | null;
  latest_revision: string | null;
  update_guide: string;
  check_error: string | null;
  message: string;
};

export function UpdateNotice() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let mounted = true;
    async function check() {
      try {
        const response = await fetch(`${GATEWAY_URL}/api/admin/updates/check`);
        if (!response.ok) throw new Error("Update check unavailable");
        const body = (await response.json()) as UpdateStatus;
        if (mounted) setStatus(body);
      } catch {
        if (mounted) setError(true);
      }
    }
    void check();
    return () => { mounted = false; };
  }, []);

  return (
    <article className="models-panel" aria-label="Yonder software updates">
      <div className="panel-head">
        <h2>Software updates</h2>
        <span className="panel-count">
          {status?.status === "revision_differs" ? "Review update" : "Revision status"}
        </span>
      </div>
      {status ? (
        <>
          <p className="entity-meta" role="status">{status.message}</p>
          <p className="entity-meta">
            Installed: {status.installed_revision?.slice(0, 12) ?? "not reported"} ·
            Latest main: {status.latest_revision?.slice(0, 12) ?? "unavailable"}
          </p>
          <p className="entity-meta">
            Updates are manual and administrator-controlled. No changes are applied from
            the web console.
          </p>
          <a className="btn" href={status.update_guide}
            target="_blank" rel="noopener noreferrer">
            View safe update instructions
          </a>
        </>
      ) : (
        <p className="entity-meta">
          {error ? "Could not check for updates." : "Checking for updates…"}
        </p>
      )}
    </article>
  );
}
