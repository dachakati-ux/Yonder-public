"use client";

import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Gauge, StatTile, TonePill } from "@/components/metrics";
import { GatewayError } from "@/lib/gateway";
import { UpdateNotice } from "@/features/operations/UpdateNotice";
import { type StatusTone } from "@/lib/hardware";
import { ratioPercent } from "@/lib/format";
import {
  type OperationsSummary,
  drainDeployment,
  enableRoute,
  fetchOperationsSummary,
  retryProviderHealth,
  runOpsTestPrompt,
  setWorkerMaintenance,
} from "@/lib/ops";

function statusTone(status: string): StatusTone {
  const value = status.toLowerCase();
  if (["ready", "healthy", "connected", "online", "ok", "active", "enabled", "loaded"].includes(value)) {
    return "ok";
  }
  if (["failed", "error", "offline", "unhealthy", "disconnected", "disabled"].includes(value)) {
    return "danger";
  }
  if (["degraded", "draining", "loading", "warming", "pending", "queued", "maintenance"].includes(value)) {
    return "warn";
  }
  return "neutral";
}

function OpsPanel({
  title,
  count,
  meta,
  children,
}: {
  title: string;
  count?: string;
  meta?: string;
  children: ReactNode;
}) {
  return (
    <article className="models-panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {count ? <span className="panel-count">{count}</span> : null}
      </div>
      {meta ? <p className="entity-meta panel-meta">{meta}</p> : null}
      {children}
    </article>
  );
}

function LedgerPanel({
  title,
  count,
  body,
  children,
}: {
  title: string;
  count: number;
  body: string;
  children?: ReactNode;
}) {
  return (
    <article className="models-panel">
      <details className="ops-collapsible">
        <summary>
          <span className="ops-collapsible-title">{title}</span>
          <span className="panel-count">{count}</span>
        </summary>
        <div className="ops-collapsible-body">
          {children}
          <pre className="logs-panel">{body}</pre>
        </div>
      </details>
    </article>
  );
}

export function OperationsWorkspace() {
  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lookupId, setLookupId] = useState("");
  const [lookupResult, setLookupResult] = useState<string | null>(null);
  const [eventFilter, setEventFilter] = useState("");
  const [routeFilter, setRouteFilter] = useState("");

  const refresh = useCallback(async () => {
    const body = await fetchOperationsSummary();
    setSummary(body);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        await refresh();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof GatewayError
              ? `${err.message} [${err.code}]`
              : err instanceof Error
                ? err.message
                : "Failed to load operations",
          );
        }
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refresh]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(
        err instanceof GatewayError
          ? `${err.message} [${err.code}] (${err.requestId})`
          : err instanceof Error
            ? err.message
            : "Action failed",
      );
    } finally {
      setBusy(false);
    }
  }

  const routes = useMemo(() => {
    if (!summary) return [];
    const needle = routeFilter.trim().toLowerCase();
    if (!needle) return summary.routes;
    return summary.routes.filter((route) =>
      `${String(route.alias)} ${String(route.deployment_id)} ${String(route.state)}`
        .toLowerCase()
        .includes(needle),
    );
  }, [routeFilter, summary]);

  return (
    <div className="ops-layout">
      <header className="models-header">
        <div>
          <h1>Operations</h1>
          <p>
            Health, capacity, routes, and operator controls — all through the
            gateway. Dangerous actions confirm first; every change is audited.
          </p>
        </div>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => void run(async () => refresh())}
        >
          Refresh
        </button>
      </header>

      <UpdateNotice />

      {error ? <div className="message-error models-error">{error}</div> : null}

      {!summary ? (
        <div className="empty-state">
          <h3>Connecting to gateway…</h3>
        </div>
      ) : (
        <>
          <section className="stat-strip" aria-label="Gateway summary">
            <StatTile
              label="Gateway"
              value={String(summary.gateway.status)}
              hint={`v${String(summary.gateway.version)} · ${String(summary.gateway.environment)}`}
              tone={statusTone(String(summary.gateway.status))}
            />
            <StatTile
              label="Security mode"
              value={String(summary.gateway.security_mode)}
              hint={
                summary.gateway.development_security_warning
                  ? "development identity injection active"
                  : "upstream trusted context"
              }
              tone={summary.gateway.development_security_warning ? "warn" : "ok"}
            />
            <StatTile
              label="Requests"
              value={`${summary.active_requests} active`}
              hint={`${summary.queued_requests} queued`}
              tone={summary.queued_requests > 0 ? "warn" : "ok"}
            />
            <StatTile
              label="Workers"
              value={`${summary.workers.connected}/${summary.workers.total}`}
              hint={`${summary.workers.disconnected} disconnected · ${summary.workers.maintenance} maintenance`}
              tone={
                summary.workers.total === 0
                  ? "neutral"
                  : summary.workers.connected === summary.workers.total
                    ? "ok"
                    : "warn"
              }
            />
            <StatTile
              label="Capacity"
              value={
                summary.capacity
                  ? summary.capacity.any_saturated
                    ? "saturated"
                    : "headroom"
                  : "not_reported"
              }
              hint={
                summary.capacity
                  ? `${summary.capacity.deployments.length} deployments measured`
                  : "no capacity snapshot"
              }
              tone={
                !summary.capacity
                  ? "neutral"
                  : summary.capacity.any_saturated
                    ? "danger"
                    : "ok"
              }
            />
            <StatTile
              label="Persistence"
              value={String(summary.gateway.persistence_backend ?? "not_reported")}
              tone="neutral"
            />
          </section>

          <section className="models-grid">
            <OpsPanel
              title="Workers"
              count={`${summary.workers.connected}/${summary.workers.total}`}
              meta={`disconnected ${summary.workers.disconnected} · maintenance ${summary.workers.maintenance}`}
            >
              <div className="stack">
                {summary.workers.items.length === 0 ? (
                  <p className="entity-meta">No workers have heartbeated yet.</p>
                ) : (
                  summary.workers.items.map((worker) => (
                    <details key={String(worker.id)} className="compact-row">
                      <summary>
                        <span className="compact-row-title">
                          {String(worker.display_name || worker.id)}
                        </span>
                        <TonePill tone={statusTone(String(worker.status))}>
                          {worker.maintenance_state
                            ? "maintenance"
                            : String(worker.status)}
                        </TonePill>
                      </summary>
                      <div className="compact-row-body">
                        <p className="entity-meta">
                          id: {String(worker.id)} · ready: {String(worker.ready)} ·
                          maintenance: {String(Boolean(worker.maintenance_state))}
                          {worker.trust_state != null
                            ? ` · trust: ${String(worker.trust_state)}`
                            : ""}
                        </p>
                        <div className="entity-actions">
                          <button
                            type="button"
                            className="btn btn-danger"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                if (
                                  !window.confirm(
                                    `Mark worker ${worker.id} for maintenance?`,
                                  )
                                ) {
                                  return;
                                }
                                await setWorkerMaintenance(String(worker.id), true);
                              })
                            }
                          >
                            Maintenance
                          </button>
                          <button
                            type="button"
                            className="btn"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await setWorkerMaintenance(String(worker.id), false);
                              })
                            }
                          >
                            Remove maintenance
                          </button>
                        </div>
                      </div>
                    </details>
                  ))
                )}
              </div>
            </OpsPanel>

            <OpsPanel title="Providers" count={String(summary.providers.length)}>
              <div className="stack">
                {summary.providers.length === 0 ? (
                  <p className="entity-meta">No providers registered.</p>
                ) : (
                  summary.providers.map((provider) => (
                    <details key={String(provider.provider)} className="compact-row">
                      <summary>
                        <span className="compact-row-title">
                          {String(provider.provider)}
                        </span>
                        <TonePill tone={statusTone(String(provider.status))}>
                          {String(provider.status)}
                        </TonePill>
                      </summary>
                      <div className="compact-row-body">
                        {provider.message ? (
                          <p className="entity-meta">{String(provider.message)}</p>
                        ) : null}
                        <div className="entity-actions">
                          <button
                            type="button"
                            className="btn"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await retryProviderHealth(String(provider.provider));
                              })
                            }
                          >
                            Retry health check
                          </button>
                        </div>
                      </div>
                    </details>
                  ))
                )}
              </div>
            </OpsPanel>

            <OpsPanel
              title="Admission / capacity"
              count={summary.capacity ? String(summary.capacity.deployments.length) : "0"}
              meta={
                summary.admission
                  ? `max active/deployment ${summary.admission.max_active_requests_per_deployment} · max queue ${summary.admission.max_queue_depth} · queue timeout ${summary.admission.queue_timeout_seconds}s · inference timeout ${summary.admission.inference_timeout_seconds}s`
                  : "Admission limits not reported."
              }
            >
              <div className="stack">
                {!summary.capacity || summary.capacity.deployments.length === 0 ? (
                  <p className="entity-meta">No deployments to measure.</p>
                ) : (
                  summary.capacity.deployments.map((item) => (
                    <div key={item.deployment_id} className="compact-static">
                      <div className="compact-row-head">
                        <span className="compact-row-title">{item.deployment_id}</span>
                        <TonePill
                          tone={item.saturated ? "danger" : statusTone(item.status)}
                        >
                          {item.saturated ? "saturated" : item.status}
                        </TonePill>
                      </div>
                      <Gauge
                        label="Active"
                        percent={ratioPercent(item.active_requests, item.max_active_requests)}
                        detail={`${item.active_requests}/${item.max_active_requests} · headroom ${item.headroom_active}`}
                      />
                      <Gauge
                        label="Queue"
                        percent={ratioPercent(item.queue_depth, item.max_queue_depth)}
                        detail={`${item.queue_depth}/${item.max_queue_depth} · headroom ${item.headroom_queue}`}
                      />
                    </div>
                  ))
                )}
              </div>
            </OpsPanel>
          </section>

          <section className="models-grid">
            <OpsPanel title="Model routes" count={`${routes.length}/${summary.routes.length}`}>
              <label className="toolbar-field">
                <span className="brand-sr">Filter routes</span>
                <input
                  className="status-select"
                  type="search"
                  placeholder="Filter alias, deployment, or state"
                  value={routeFilter}
                  onChange={(event) => setRouteFilter(event.target.value)}
                />
              </label>
              <div className="stack" style={{ marginTop: "0.7rem" }}>
                {routes.length === 0 ? (
                  <p className="entity-meta">
                    {summary.routes.length === 0
                      ? "No aliases configured."
                      : "No alias matches this filter."}
                  </p>
                ) : (
                  routes.map((route) => (
                    <details key={String(route.alias)} className="compact-row">
                      <summary>
                        <span className="compact-row-title">{String(route.alias)}</span>
                        <TonePill tone={statusTone(String(route.state))}>
                          {String(route.state)}
                        </TonePill>
                      </summary>
                      <div className="compact-row-body">
                        <p className="entity-meta">
                          deployment: {String(route.deployment_id)}
                          {route.lane || route.experimental != null
                            ? ` · lane: ${String(
                                route.lane ??
                                  (route.experimental ? "experimental" : "production"),
                              )}`
                            : ""}
                        </p>
                        <div className="entity-actions">
                          <button
                            type="button"
                            className="btn"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await enableRoute(String(route.alias), true);
                              })
                            }
                          >
                            Enable
                          </button>
                          <button
                            type="button"
                            className="btn btn-danger"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                if (
                                  !window.confirm(
                                    `Disable route ${route.alias}? Chat will fail for this alias.`,
                                  )
                                ) {
                                  return;
                                }
                                await enableRoute(String(route.alias), false);
                              })
                            }
                          >
                            Disable
                          </button>
                        </div>
                      </div>
                    </details>
                  ))
                )}
              </div>
            </OpsPanel>

            <OpsPanel title="Deployments" count={String(summary.deployments.length)}>
              <div className="stack">
                {summary.deployments.length === 0 ? (
                  <p className="entity-meta">No deployments registered.</p>
                ) : (
                  summary.deployments.map((deployment) => (
                    <details key={String(deployment.id)} className="compact-row">
                      <summary>
                        <span className="compact-row-title">{String(deployment.id)}</span>
                        <TonePill tone={statusTone(String(deployment.status))}>
                          {String(deployment.status)}
                        </TonePill>
                      </summary>
                      <div className="compact-row-body">
                        <p className="entity-meta">
                          active {String(deployment.active_requests)} · requests{" "}
                          {String(deployment.request_count)}
                        </p>
                        <div className="entity-actions">
                          <button
                            type="button"
                            className="btn btn-danger"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                if (
                                  !window.confirm(`Drain deployment ${deployment.id}?`)
                                ) {
                                  return;
                                }
                                await drainDeployment(String(deployment.id));
                              })
                            }
                          >
                            Drain
                          </button>
                        </div>
                      </div>
                    </details>
                  ))
                )}
              </div>
              <button
                type="button"
                className="btn btn-primary"
                style={{ marginTop: "0.75rem" }}
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await runOpsTestPrompt("yonder-chat", "operations test prompt");
                  })
                }
              >
                Run test prompt
              </button>
            </OpsPanel>

            <OpsPanel title="Request lookup">
              <div className="composer-row">
                <input
                  className="status-select"
                  style={{ width: "100%", padding: "0.65rem" }}
                  placeholder="request id"
                  value={lookupId}
                  onChange={(event) => setLookupId(event.target.value)}
                />
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    const match = summary.recent_requests.find(
                      (item) => item.request_id === lookupId.trim(),
                    );
                    const fail = summary.recent_failures.find(
                      (item) => item.request_id === lookupId.trim(),
                    );
                    const hit = match || fail;
                    setLookupResult(
                      hit
                        ? JSON.stringify(hit, null, 2)
                        : "No matching request in the recent ledger.",
                    );
                  }}
                >
                  Lookup
                </button>
              </div>
              {lookupResult ? <pre className="logs-panel">{lookupResult}</pre> : null}
            </OpsPanel>
          </section>

          <section className="models-grid">
            <LedgerPanel
              title="Recent failures"
              count={summary.recent_failures.length}
              body={
                summary.recent_failures.length
                  ? summary.recent_failures
                      .map(
                        (item) =>
                          `${item.created_at} ${item.request_id} ${item.error_code} ${item.message}`,
                      )
                      .join("\n")
                  : "No recent failures."
              }
            />
            <LedgerPanel
              title="Recent requests"
              count={summary.recent_requests.length}
              body={
                summary.recent_requests.length
                  ? summary.recent_requests
                      .map(
                        (item) =>
                          `${item.created_at} ${item.request_id} ${item.status} ${item.model_alias ?? ""} ${item.duration_ms ?? ""}ms`,
                      )
                      .join("\n")
                  : "No recent requests."
              }
            />
            <LedgerPanel
              title="Admin events"
              count={summary.recent_admin_events.length}
              body={
                summary.recent_admin_events.length
                  ? summary.recent_admin_events
                      .filter((item) =>
                        eventFilter.trim()
                          ? String(item.event_type ?? "")
                              .toLowerCase()
                              .includes(eventFilter.trim().toLowerCase())
                          : true,
                      )
                      .map(
                        (item) =>
                          `${item.created_at} ${item.event_type} ${item.resource_id ?? ""} ${item.result ?? ""}`,
                      )
                      .join("\n") || "No events match this filter."
                  : "No admin events yet."
              }
            >
              <label className="toolbar-field">
                <span className="brand-sr">Filter admin events</span>
                <input
                  className="status-select"
                  type="search"
                  placeholder="Filter by event_type (client-side)"
                  value={eventFilter}
                  onChange={(event) => setEventFilter(event.target.value)}
                />
              </label>
            </LedgerPanel>
          </section>
        </>
      )}
    </div>
  );
}
