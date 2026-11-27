"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { completeChat } from "@/lib/gateway";
import { fetchHardware } from "@/lib/ops";
import type { HardwareNode } from "@/lib/hardware";
import { weightBudgets } from "@/lib/modelFit";
import { fetchSetup, readyModels, type SetupStatus } from "./setup";

const gib = (n: number) => (n / 1024 ** 3).toFixed(1) + " GiB";

export function SetupWorkspace() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [hardware, setHardware] = useState<HardwareNode[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState("");
  const [testError, setTestError] = useState("");
  const candidates = status ? readyModels(status) : [];
  const activeAlias = candidates.some((model) => model.alias === selected)
    ? selected : candidates[0]?.alias ?? "";
  const budgets = hardware ? weightBudgets(hardware) : [];

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10000);
    setLoading(true);
    setStatus(null);
    setHardware(null);
    setError("");
    setResult("");
    setTestError("");
    void fetchSetup(controller.signal).then((snapshot) => {
      if (active) setStatus(snapshot);
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error && err.name !== "AbortError"
        ? err.message : "Gateway did not answer in time. Check its status and retry.");
    }).finally(() => {
      window.clearTimeout(timer);
      if (active) setLoading(false);
    });
    void fetchHardware().then((data) => {
      if (active) setHardware(data.nodes);
    }).catch(() => {
      if (active) setHardware(null);
    });
    return () => { active = false; controller.abort(); window.clearTimeout(timer); };
  }, [revision]);

  async function verifyModel() {
    if (!activeAlias || testing) return;
    setTesting(true);
    setResult("");
    setTestError("");
    try {
      const latest = await fetchSetup(AbortSignal.timeout(10000));
      if (!readyModels(latest).some((alias) => alias.alias === activeAlias)) {
        setStatus(latest);
        throw new Error("The selected route is no longer ready. Refresh and choose another model.");
      }
      const response = await completeChat({
        model: activeAlias,
        messages: [{ role: "user", content: "Explain what a CPU does in one short sentence." }],
        max_tokens: 128,
      });
      if (!response.content.trim()) throw new Error("The model returned no text. Check Operations.");
      setResult(response.content);
    } catch (err) {
      setTestError(err instanceof Error ? err.message : "Inference failed. Check Operations.");
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="setup-workspace" aria-labelledby="setup-heading">
      <div className="setup-heading">
        <div>
          <p className="setup-eyebrow">GET STARTED</p>
          <h1 id="setup-heading">Your first Yonder session</h1>
          <p>Check your connection, choose a model, and make a real inference request.</p>
        </div>
        <button type="button" className="btn" disabled={loading || testing}
          onClick={() => setRevision((n) => n + 1)}>Refresh checks</button>
      </div>
      <p className="setup-notice" role="status">
        {loading ? "Checking Gateway…" : status?.security_mode === "development"
          ? "Local development mode: only use on a trusted single-user machine."
          : status ? "Access is controlled by your configured upstream identity provider."
          : "Gateway connection has not been established."}
      </p>
      {error && <p role="alert" className="message-error">{error}</p>}
      <section className="setup-step" aria-labelledby="setup-connection">
        <h2 id="setup-connection">1. Check the Gateway</h2>
        <p>{loading ? "Checking…" : status?.gateway_ready
          ? "Gateway is accepting requests." : "Gateway readiness has not been established."}</p>
        <p>Service health alone does not establish model readiness. If this check fails,
          verify the launcher and Gateway configuration or ask your administrator.</p>
        <Link className="btn btn-ghost" href="/operations">Open Operations</Link>
      </section>
      <section className="setup-step" aria-labelledby="setup-hardware">
        <h2 id="setup-hardware">2. Choose suitable hardware and weights</h2>
        {hardware === null ? <p>Hardware telemetry unavailable, pending or not authorized.</p> :
          budgets.length === 0 ? <p>No eligible GPU has reported free memory. CPU and external
            inference may still be available; use the runtime's compatibility checks.</p> :
          <ul>{budgets.slice(0, 5).map((gpu) =>
            <li key={gpu.workerId + ":" + gpu.index}>{gpu.worker} · {gpu.gpu} #{gpu.index}:
              {" "}{gib(gpu.freeBytes)} free, screen for weights up to {gib(gpu.maxWeightBytes)}</li>
          )}</ul>}
        <p>Weight screening reserves 25% overhead relative to weights on one GPU.
          Context, buffers, architecture and backend support require separate checks.
          No GPU memories are combined or assumed.</p>
        <div className="setup-actions">
          <Link className="btn" href="/hardware">Inspect hardware</Link>
          <Link className="btn" href="/models">Explore models</Link>
        </div>
      </section>
      <section className="setup-step" aria-labelledby="setup-model">
        <h2 id="setup-model">3. Connect a real inference model</h2>
        {status && status.aliases.some((alias) => alias.kind === "mock") &&
          <p className="setup-notice">Mock routes are demonstrations only.
            They are not loaded AI models and cannot pass the inference step.</p>}
        {candidates.length > 0 ? <p>{candidates.length} non-mock chat route(s) reported ready.
          A request is still required to verify inference.</p> : <>
          <p>No ready non-mock chat route has been reported. Use a configured worker or
            connect an existing OpenAI-compatible server such as LM Studio or llama.cpp.</p>
          <ol>
            <li>Load a compatible model in your runtime and start its local server.</li>
            <li>Ask your operator to verify the model ID and configure Yonder's endpoint
              and alias as documented in the Quick Start guide.</li>
            <li>Refresh after configuration. Never expose a development model server publicly.</li>
          </ol>
          <a className="btn" href="https://github.com/dachakati-ux/Yonder-public/blob/main/docs/quickstart.md#c-first-real-model-response-lm-studio-or-another-openai-compatible-server"
            target="_blank" rel="noreferrer">Model connection guide (new tab)</a>
        </>}
      </section>
      <section className="setup-step" aria-labelledby="setup-test">
        <h2 id="setup-test">4. Verify inference</h2>
        <label htmlFor="setup-alias">Non-mock chat alias</label>
        <select id="setup-alias" value={activeAlias} disabled={!candidates.length || testing || loading}
          onChange={(event) => { setSelected(event.target.value); setResult(""); setTestError(""); }}>
          {!candidates.length && <option value="">No ready model</option>}
          {candidates.map((alias) => <option key={alias.alias} value={alias.alias}>{alias.alias}</option>)}
        </select>
        <p>Test sends a short, non-sensitive prompt through the Gateway and may consume inference resources.</p>
        <button className="btn btn-primary" type="button"
          disabled={!activeAlias || testing || loading} onClick={() => void verifyModel()}>
          {testing ? "Waiting for model…" : "Send test prompt"}
        </button>
        <div role="status" aria-live="polite">
          {testing && <p>Inference request in progress.</p>}
          {result && <><p>Received a response for {activeAlias}. Route was reported non-mock
            at preflight; this is not a model identity or quality certification.</p>
            <p className="setup-result">{result}</p></>}
        </div>
        {testError && <p role="alert" className="message-error">{testError}</p>}
        {result && <Link className="btn" href={"/?model=" + encodeURIComponent(activeAlias)}>
          Open this model in Chat</Link>}
      </section>
      <section className="setup-step" aria-labelledby="setup-next">
        <h2 id="setup-next">5. Explore at your pace</h2>
        <p>The learning, coding and operational tools are independent of this setup guide.
          Nothing is installed or changed by the diagnostics.</p>
        <div className="setup-actions">
          <Link className="btn" href="/">Open Chat</Link>
          <Link className="btn" href="/academy">Learn with Academy</Link>
          <Link className="btn" href="/help">Get help</Link>
        </div>
      </section>
    </section>
  );
}
