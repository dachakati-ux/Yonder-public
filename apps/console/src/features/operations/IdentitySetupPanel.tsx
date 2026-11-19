"use client";

import { useEffect, useState } from "react";
import {
  getAuthProviders,
  getConvyxStatus,
  previewRoleMapping,
  type AuthProvidersResponse,
  type RoleMappingPreviewResponse,
} from "@/lib/gateway";
import { TonePill } from "@/components/metrics";

export function IdentitySetupPanel() {
  const [providers, setProviders] = useState<AuthProvidersResponse | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<string>("authentik");
  const [issuerUrl, setIssuerUrl] = useState<string>("https://idp.example.org/application/o/yonder");
  const [clientId, setClientId] = useState<string>("yonder-gateway");
  const [groupsInput, setGroupsInput] = useState<string>("yonder-teachers, yonder-students");
  const [preview, setPreview] = useState<RoleMappingPreviewResponse | null>(null);
  const [convyx, setConvyx] = useState<{ enabled: boolean; availability: string; detail?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const info = await getAuthProviders();
        setProviders(info);
        if (info.active_template) {
          setSelectedTemplate(info.active_template);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load OIDC provider metadata.");
      }
      try {
        const cx = await getConvyxStatus();
        setConvyx(cx);
      } catch {
        // Optional integration; ignore if unconfigured/denied.
      }
    })();
  }, []);

  const templateInfo = providers?.templates[selectedTemplate];

  const handlePreview = async () => {
    setError(null);
    const groups = groupsInput
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    try {
      const result = await previewRoleMapping(groups);
      setPreview(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Role mapping preview failed.");
    }
  };

  const snippet = [
    `YONDER_SECURITY__MODE=oidc`,
    `YONDER_SECURITY__OIDC__PROVIDER_TEMPLATE=${selectedTemplate}`,
    `YONDER_SECURITY__OIDC__ISSUER=${issuerUrl}`,
    `YONDER_SECURITY__OIDC__CLIENT_ID=${clientId}`,
    `YONDER_SECURITY__OIDC__REDIRECT_URI=https://yonder.example.org/v1/auth/callback`,
    `YONDER_SECURITY__OIDC__SESSION_SECRET=<generate-with-openssl-rand-hex-32>`,
  ].join("\n");

  return (
    <section className="stack">
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>Identity &amp; Optional Integrations</h2>
            <p className="entity-meta">
              OIDC Authorization Code + PKCE setup guide, role-mapping preview, and optional Convyx adapter status.
            </p>
          </div>
          <div className="entity-actions">
            <TonePill tone={providers?.security_mode === "development" ? "warn" : "ok"}>
              mode: {providers?.security_mode ?? "loading"}
            </TonePill>
            {convyx && (
              <TonePill tone={convyx.availability === "ready" ? "ok" : "neutral"}>
                convyx: {convyx.availability}
              </TonePill>
            )}
          </div>
        </div>

        {error && <p className="entity-meta">{error}</p>}

        {providers && (
          <div className="stack">
            <div className="toolbar">
              <label className="toolbar-field">
                <span>IdP Template</span>
                <select
                  className="status-select"
                  value={selectedTemplate}
                  onChange={(e) => setSelectedTemplate(e.target.value)}
                >
                  {Object.entries(providers.templates).map(([key, item]) => (
                    <option key={key} value={key}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="toolbar-field">
                <span>Issuer URL</span>
                <input
                  className="status-select"
                  type="url"
                  value={issuerUrl}
                  onChange={(e) => setIssuerUrl(e.target.value)}
                />
              </label>
              <label className="toolbar-field">
                <span>Client ID</span>
                <input
                  className="status-select"
                  type="text"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                />
              </label>
            </div>

            {templateInfo && (
              <p className="entity-meta">
                Scopes: <code>{templateInfo.scopes.join(" ")}</code> · Groups claim:{" "}
                <code>{templateInfo.groups_claim}</code> — {templateInfo.notes}
              </p>
            )}

            <pre className="code-block">{snippet}</pre>

            <div className="toolbar">
              <label className="toolbar-field">
                <span>Preview IdP Groups (comma-separated)</span>
                <input
                  className="status-select"
                  type="text"
                  value={groupsInput}
                  onChange={(e) => setGroupsInput(e.target.value)}
                />
              </label>
              <button type="button" className="btn" onClick={() => void handlePreview()}>
                Preview Role Mapping
              </button>
            </div>

            {preview && (
              <p className="entity-meta">
                Resolved roles: <code>{preview.roles.join(", ") || "(none — default deny)"}</code> ·
                Capabilities: <code>{preview.capabilities.join(", ") || "(none)"}</code> —{" "}
                {preview.note}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
