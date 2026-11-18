"use client";

import { useCallback, useEffect, useState } from "react";
import { GATEWAY_URL } from "@/lib/brand";

type Addon = {
  id: string;
  name: string;
  version: string;
  category: string;
  summary: string;
  status: "available" | "planned" | "installed";
  permissions: string[];
  warning: string;
  docs: string;
  installed_version: string | null;
  installable: boolean;
  removable: boolean;
  requires_restart: boolean;
  api_version: number;
  min_core_version: string;
  max_core_version_exclusive: string | null;
  compatibility: string;
  dependencies: string[];
  missing_dependencies: string[];
  config_schema_version: number;
  service_id: string | null;
  requires_attention: boolean;
  in_public_catalog: boolean | null;
};

type Catalog = {
  addons: Addon[];
  source: string;
  remote_catalog: {
    checked: boolean;
    available: boolean;
    error: string | null;
  };
  security: {
    remote_code_execution: boolean;
    install_requires_admin: boolean;
    unreviewed_addons_blocked: boolean;
  };
};

type AddonServiceHealth = {
  addon_id: string;
  status: string;
  service?: { version: string; health: string; capabilities: string[] };
};

type ControllerStatus = {
  configured: boolean;
  available?: boolean;
  deployments: Array<{ addon_id: string; version: string; adapter: string }>;
};
type DeploymentPlan = {
  schema_version: number;
  addon_id: string;
  version: string;
  action: "install" | "remove";
  adapter: string;
  image: string;
  image_acquisition: "preloaded" | "signed_pull";
  limits: { memory_mb: number; cpus: number; pids: number; network: string };
  plan_sha256: string;
  configuration: {
    public_settings: Record<string, string>;
    secret_volume: string | null;
    data_volume: string | null;
  };
  publisher_verification: string;
  publisher_key_id?: string;
  readiness: { mode: string; timeout_seconds: number; rollback_on_failure: boolean };
  requires_approval: boolean;
  note: string;
};
type ControllerJob = {
  id: string;
  addon_id: string;
  action: string;
  status: string;
  message: string;
};
type SecurityReport = {
  checks: Array<{ id: string; status: string; message: string }>;
  note: string;
};

async function api<T>(path: string, method = "GET", payload?: object): Promise<T> {
  const response = await fetch(GATEWAY_URL + path, {
    method, cache: "no-store",
    ...(payload ? { headers: { "content-type": "application/json" }, body: JSON.stringify(payload) } : {}),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.error?.message ?? "Request failed (" + response.status + ")");
  }
  return body as T;
}

export function AddonsWorkspace() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [security, setSecurity] = useState<SecurityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [serviceHealth, setServiceHealth] = useState<Record<string, AddonServiceHealth>>({});
  const [controller, setController] = useState<ControllerStatus | null>(null);
  const [deployments, setDeployments] = useState<ControllerJob[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await api<Catalog>("/api/admin/addons");
      setCatalog(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load add-ons");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    let active = true;
    async function pollController() {
      try {
        const status = await api<ControllerStatus>("/api/admin/addons/controller/status");
        if (!active) return;
        setController(status);
        if (status.configured && status.available) {
          const result = await api<{ jobs: ControllerJob[] }>(
            "/api/admin/addons/controller/jobs"
          );
          if (active) setDeployments(result.jobs);
        }
      } catch {
        if (active) setController({ configured: false, deployments: [] });
      }
    }
    void pollController();
    const timer = window.setInterval(() => void pollController(), 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function manageService(item: Addon, action: "install" | "remove") {
    setBusyId(item.id);
    try {
      const planResult = await api<{ plan: DeploymentPlan }>(
        "/api/admin/addons/" + encodeURIComponent(item.id) +
        "/controller/plan?action=" + action
      );
      const plan = planResult.plan;
      const details = [
        "APPROVE " + action.toUpperCase() + ": " + item.name,
        "Image: " + plan.image,
        "Image acquisition: " + plan.image_acquisition,
        "Version: " + plan.version,
        "Memory: " + plan.limits.memory_mb + " MiB",
        "CPU limit: " + plan.limits.cpus,
        "Network: " + plan.limits.network,
        "Configuration: " + JSON.stringify(plan.configuration.public_settings),
        "Secrets volume: " + (plan.configuration.secret_volume ?? "not requested"),
        "Persistent data volume: " + (plan.configuration.data_volume ?? "not requested"),
        "Publisher: " + plan.publisher_verification + (plan.publisher_key_id ? " (" + plan.publisher_key_id + ")" : ""),
        "Readiness: " + plan.readiness.mode + " (" + plan.readiness.timeout_seconds + "s)",
        "Rollback if unhealthy: " + (plan.readiness.rollback_on_failure ? "Yes" : "No"),
        "Plan checksum: " + plan.plan_sha256,
        "",
        "This changes Docker containers on the controller host.",
        "Proceed with this exact approved plan?"
      ].join("\n");
      if (!window.confirm(details)) return;
      const key = crypto.randomUUID().replaceAll("-", "");
      const result = await api<{ job: ControllerJob }>(
        "/api/admin/addons/" + encodeURIComponent(item.id) + "/controller/jobs",
        "POST",
        {
          addon_id: item.id,
          action,
          plan_sha256: plan.plan_sha256,
          idempotency_key: key,
        }
      );
      setDeployments((current) => [result.job, ...current]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Controller rejected deployment");
    } finally {
      setBusyId(null);
    }
  }


  useEffect(() => {
    if (!catalog?.addons.some((x) => x.id === "security-insights" && x.status === "installed")) {
      setSecurity(null);
      return;
    }
    let alive = true;
    void api<SecurityReport>("/api/admin/addons/security-insights/report")
      .then((value) => { if (alive) setSecurity(value); })
      .catch(() => { if (alive) setSecurity(null); });
    return () => { alive = false; };
  }, [catalog]);

  async function mutate(item: Addon, mode: "install" | "uninstall") {
    if (mode === "install") {
      const permissions = item.permissions.join(", ") || "none requested";
      const description = [
        "Install " + item.name + " v" + item.version + "?",
        "",
        "Required permissions: " + permissions,
        "Only reviewed code already included in this Yonder release can be enabled.",
        item.warning,
      ].join("\n");
      if (!window.confirm(description)) return;
    } else if (!window.confirm("Disable " + item.name + "? Your configuration will be retained.")) {
      return;
    }
    setBusyId(item.id);
    try {
      const data = await api<{ catalog: Catalog }>(
        "/api/admin/addons/" + encodeURIComponent(item.id) + "/" + mode, "POST"
      );
      setCatalog(data.catalog);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Add-on operation failed");
    } finally {
      setBusyId(null);
    }
  }

  async function inspectService(item: Addon) {
    setBusyId(item.id);
    try {
      const status = await api<AddonServiceHealth>(
        "/api/admin/addons/" + encodeURIComponent(item.id) + "/service/health"
      );
      setServiceHealth((current) => ({ ...current, [item.id]: status }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not verify service");
    } finally {
      setBusyId(null);
    }
  }

  async function acknowledgeRecovery(job: ControllerJob) {
    setBusyId(job.id);
    try {
      const observed = await api<{ observed_state: string }>(
        "/api/admin/addons/" + encodeURIComponent(job.addon_id) +
        "/controller/observed"
      );
      const state = observed.observed_state;
      if (!["running", "stopped", "not_deployed"].includes(state)) {
        throw new Error("Unsafe or unknown Docker state: " + state);
      }
      const confirmed = window.confirm([
        "An earlier " + job.action + " was interrupted.",
        "Add-on: " + job.addon_id,
        "Docker currently reports: " + state,
        "",
        "Review the container and any stored data before acknowledging.",
        "This only unblocks a new deployment plan; it DOES NOT undo changes.",
        "Acknowledge this observed state?"
      ].join("\n"));
      if (!confirmed) return;
      const response = await api<{ job: ControllerJob }>(
        "/api/admin/addons/controller/jobs/" + encodeURIComponent(job.id) +
        "/acknowledge",
        "POST",
        { observed_state: state }
      );
      setDeployments((current) =>
        current.map((existing) => existing.id === job.id ? response.job : existing)
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Recovery review failed");
    } finally {
      setBusyId(null);
    }
  }

  async function refreshRemote() {
    setBusyId("refresh");
    try {
      setCatalog(await api<Catalog>("/api/admin/addons/refresh", "POST"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check GitHub");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="models-layout">
      <header className="models-header">
        <div>
          <h1>Add-ons</h1>
          <p>Expand Yonder without complicating Core. Browse reviewed modules,
            inspect required permissions, and activate installed capabilities.</p>
        </div>
        <button type="button" className="btn" onClick={() => void refreshRemote()}
          disabled={busyId != null}>Check GitHub catalog</button>
      </header>

      <section className="models-panel" aria-label="Add-on safety">
        <h2>Safe installation policy</h2>
        <p className="entity-meta">
          Add-ons are discovered through the official GitHub catalog, but clicking Install
          never runs remote scripts or arbitrary repository code. Only extensions bundled
          and reviewed in your current Yonder release can be enabled.
        </p>
        <p className="entity-meta">
          Institution-wide deployments must configure verified identity and authorizations
          before granting access. An add-on is not a shortcut around Core security.
        </p>
        {catalog?.remote_catalog.error ? (
          <p className="entity-meta" role="status">
            {catalog.remote_catalog.error} (offline catalog available)
          </p>
        ) : null}
      </section>

      {error ? <div className="message-error" role="alert">{error}</div> : null}
      {loading ? <div className="empty-state">Loading add-ons…</div> : null}
      <div className="addons-grid">
        {catalog?.addons.map((item) => (
          <article key={item.id} className="models-panel addons-card">
            <div className="addons-card-heading">
              <div>
                <span className="entity-meta">{item.category.toUpperCase()}</span>
                <h2>{item.name}</h2>
              </div>
              <span className={"status-pill status-" + (item.status === "installed" ? "ready" : "pending")}>
                {item.status === "installed" ? "Installed" : item.status === "planned" ? "Coming soon" : "Available"}
              </span>
            </div>
            <p>{item.summary}</p>
            <p className="entity-meta">Version {item.version} · API v{item.api_version} · ID: {item.id}</p>
            <p className="entity-meta">Core compatibility: {item.min_core_version} to {item.max_core_version_exclusive ?? "latest"} (exclusive upper bound)</p>
            {item.compatibility !== "compatible" ? (
              <p className="message-error">Blocked: {item.compatibility.replaceAll("_", " ")}</p>
            ) : null}
            {item.missing_dependencies.length > 0 ? (
              <p className="message-error">Install dependencies first: {item.missing_dependencies.join(", ")}</p>
            ) : null}
            {item.requires_attention ? (
              <p className="message-error">Version mismatch: reviewed migration required.</p>
            ) : null}
            <p className="entity-meta">
              Permissions: {item.permissions.join(", ") || "none"}
            </p>
            <p className="entity-meta">{item.warning}</p>
            {serviceHealth[item.id] ? (
              <p className="entity-meta" role="status">
                Service: {serviceHealth[item.id].status}
                {serviceHealth[item.id].service ?
                  " · v" + serviceHealth[item.id].service?.version : ""}
              </p>
            ) : null}
            <div className="addons-card-actions">
              {item.installable ? (
                <button className="btn btn-primary" type="button"
                  disabled={busyId != null} onClick={() => void mutate(item, "install")}>
                  {busyId === item.id ? "Installing…" : "Install"}
                </button>
              ) : item.removable ? (
                <button className="btn" type="button" disabled={busyId != null}
                  onClick={() => void mutate(item, "uninstall")}>
                  {busyId === item.id ? "Removing…" : "Disable"}
                </button>
              ) : (
                <button className="btn" type="button" disabled>Not yet available</button>
              )}
              {controller?.available && item.status === "installed" &&
                controller.deployments.some((entry) => entry.addon_id === item.id) ? (
                <>
                  <button className="btn" type="button"
                    disabled={busyId != null}
                    onClick={() => void manageService(item, "install")}>
                    Deploy service
                  </button>
                  <button className="btn btn-ghost" type="button"
                    disabled={busyId != null}
                    onClick={() => void manageService(item, "remove")}>
                    Remove service
                  </button>
                </>
              ) : null}
              {item.service_id && item.status === "installed" ? (
                <button className="btn btn-ghost" type="button"
                  disabled={busyId != null}
                  onClick={() => void inspectService(item)}>
                  Check service
                </button>
              ) : null}
              <a className="btn btn-ghost" target="_blank" rel="noopener noreferrer"
                href={"https://github.com/dachakati-ux/Yonder-public/blob/main/" + item.docs}>
                Documentation
              </a>
            </div>
          </article>
        ))}
      </div>

      <section className="models-panel" aria-label="Add-on controller">
        <h2>Deployment controller</h2>
        <p className="entity-meta">
          {controller?.available ? "Controller connected. Reviewed deployment plans can be approved above."
            : controller?.configured ? "Controller configured but unavailable."
            : "Controller is optional and currently not connected."}
        </p>
        <p className="entity-meta">
          Only approved image digests can be deployed. Docker is never controlled
          directly by the browser or Yonder Gateway.
        </p>
        {deployments.length ? (
          <div className="addons-checks">
            {deployments.slice(0, 12).map((job) => (
              <div key={job.id} className="addons-check">
                <strong>{job.status}</strong>
                <span>
                  {job.addon_id}: {job.action} · {job.message || job.id}
                  {job.status === "interrupted" ? (
                    <button className="btn btn-ghost" type="button"
                      disabled={busyId != null}
                      onClick={() => void acknowledgeRecovery(job)}>
                      Review interrupted job
                    </button>
                  ) : null}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </section>

      {security ? (
        <section className="models-panel" aria-label="Security Insights report">
          <h2>Security Insights</h2>
          <p className="entity-meta">{security.note}</p>
          <div className="addons-checks">
            {security.checks.map((check) => (
              <div key={check.id} className="addons-check">
                <strong>{check.status.toUpperCase()}</strong>
                <span>{check.message}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
