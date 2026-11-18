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

type SecurityReport = {
  checks: Array<{ id: string; status: string; message: string }>;
  note: string;
};

async function api<T>(path: string, method = "GET"): Promise<T> {
  const response = await fetch(GATEWAY_URL + path, { method, cache: "no-store" });
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
