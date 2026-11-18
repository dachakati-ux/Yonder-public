"use client";

import { useEffect, useState } from "react";
import { GATEWAY_URL } from "@/lib/brand";

type SetupField = {
  key: string;
  label: string;
  help_text: string;
  kind: "text" | "choice" | "integer" | "boolean";
  required: boolean;
  default: string | null;
  choices: string[];
};

type SetupState = {
  addon_id: string;
  fields: SetupField[];
  values: Record<string, string>;
  configured: boolean;
  locked_settings: string[];
  secrets_required: boolean;
  note: string;
};

async function requestSetup(
  path: string, method = "GET", payload?: object
): Promise<SetupState> {
  const response = await fetch(GATEWAY_URL + path, {
    method,
    cache: "no-store",
    ...(payload ? {
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    } : {}),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.error?.message ?? "Setup request rejected (" + response.status + ")");
  }
  return body as SetupState;
}

export function AddonSetupWizard({
  addonId,
  onClose,
}: {
  addonId: string;
  onClose: () => void;
}) {
  const [setup, setSetup] = useState<SetupState | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    setSetup(null);
    setSaved(false);
    setError("");
    void requestSetup(
      "/api/admin/addons/" + encodeURIComponent(addonId) + "/controller/setup"
    ).then((data) => {
      if (active) {
        setSetup(data);
        setValues(data.values);
      }
    }).catch((err: unknown) => {
      if (active) {
        setError(err instanceof Error ? err.message : "Setup unavailable");
      }
    });
    return () => { active = false; };
  }, [addonId]);

  async function save() {
    if (!setup) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const result = await requestSetup(
        "/api/admin/addons/" + encodeURIComponent(addonId) + "/controller/setup",
        "PUT",
        { values }
      );
      setSetup(result);
      setValues(result.values);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Configuration rejected");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="models-panel" aria-label={"Add-on setup " + addonId}>
      <div className="panel-head">
        <h2>Configure {addonId}</h2>
        <button className="btn btn-ghost" type="button" onClick={onClose}>Close</button>
      </div>
      <p className="entity-meta">
        This wizard accepts only fields declared in the approved package.
        Do not enter passwords, keys or tokens here.
      </p>
      {!setup && !error ? <p className="entity-meta">Loading setup requirements…</p> : null}
      {error ? <p className="message-error" role="alert">{error}</p> : null}
      {setup ? (
        <>
          {setup.fields.length === 0 ? (
            <p className="entity-meta">No editable settings are required for this add-on.</p>
          ) : (
            <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
              <div className="addons-checks">
                {setup.fields.map((field) => (
                  <label key={field.key} style={{ display: "grid", gap: 5 }}>
                    <strong>{field.label}{field.required ? " *" : ""}</strong>
                    {field.kind === "choice" || field.kind === "boolean" ? (
                      <select
                        value={values[field.key] ?? ""}
                        required={field.required}
                        onChange={(event) => {
                          setSaved(false);
                          setValues((current) => ({
                            ...current, [field.key]: event.target.value,
                          }));
                        }}
                      >
                        {!field.required ? <option value="">Not set</option> : null}
                        {(field.kind === "boolean" ? ["true", "false"] : field.choices)
                          .map((choice) => (
                            <option key={choice} value={choice}>{choice}</option>
                          ))}
                      </select>
                    ) : (
                      <input
                        type={field.kind === "integer" ? "number" : "text"}
                        min={field.kind === "integer" ? 0 : undefined}
                        maxLength={128}
                        required={field.required}
                        value={values[field.key] ?? ""}
                        onChange={(event) => {
                          setSaved(false);
                          setValues((current) => ({
                            ...current, [field.key]: event.target.value,
                          }));
                        }}
                      />
                    )}
                    <span className="entity-meta">{field.help_text || field.key}</span>
                  </label>
                ))}
              </div>
              <div className="addons-card-actions" style={{ marginTop: 14 }}>
                <button className="btn btn-primary" type="submit" disabled={busy}>
                  {busy ? "Saving…" : "Save approved settings"}
                </button>
                {saved ? <span role="status">Configuration saved. Review a new deployment plan.</span> : null}
              </div>
            </form>
          )}
          <p className="entity-meta">
            {setup.configured ? "Required setup fields are complete." : "Complete required fields before deployment."}
            {setup.secrets_required ? " Credentials must be provided in a separately approved secrets volume." : ""}
          </p>
          {setup.locked_settings.length > 0 ? (
            <p className="entity-meta">
              Locked administrator settings: {setup.locked_settings.join(", ")}
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
