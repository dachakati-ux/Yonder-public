"use client";

import { useCallback, useEffect, useState } from "react";
import { GATEWAY_URL } from "@/lib/brand";

interface OrgSettings {
  tenant_id: string;
  org_name: string;
  profile_template: "k12_school" | "university" | "small_business";
  retention_days: number;
  privacy_notice: string;
  max_tokens_per_day_per_user: number;
  allow_student_workspaces: boolean;
  allow_student_agents: boolean;
}

interface AcademyClass {
  class_id: string;
  title: string;
  instructor_subject: string;
  model_guidance: string;
}

export function InstitutionPanel() {
  const [org, setOrg] = useState<OrgSettings | null>(null);
  const [classes, setClasses] = useState<AcademyClass[]>([]);
  const [orgName, setOrgName] = useState("Yonder Academy");
  const [template, setTemplate] = useState<"k12_school" | "university" | "small_business">(
    "k12_school",
  );
  const [classTitle, setClassTitle] = useState("");
  const [guidance, setGuidance] = useState("Use yonder-chat for guided coursework.");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [orgRes, clsRes] = await Promise.all([
        fetch(`${GATEWAY_URL}/v1/academy/org/settings`),
        fetch(`${GATEWAY_URL}/v1/academy/org/classes`),
      ]);
      if (orgRes.ok) {
        const data = (await orgRes.json()) as OrgSettings;
        setOrg(data);
        setOrgName(data.org_name);
        setTemplate(data.profile_template);
      }
      if (clsRes.ok) {
        const data = (await clsRes.json()) as { classes: AcademyClass[] };
        setClasses(data.classes ?? []);
      }
    } catch {
      // Gateway may be offline during static build/preview.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const saveOrg = async () => {
    setStatusMsg(null);
    const res = await fetch(`${GATEWAY_URL}/v1/academy/org/settings`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ org_name: orgName, profile_template: template }),
    });
    if (res.ok) {
      setStatusMsg("Institution settings saved.");
      await load();
    } else {
      setStatusMsg(`Failed to save settings (${res.status}).`);
    }
  };

  const createClass = async () => {
    if (!classTitle.trim()) return;
    setStatusMsg(null);
    const res = await fetch(`${GATEWAY_URL}/v1/academy/org/classes`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: classTitle, model_guidance: guidance }),
    });
    if (res.ok) {
      setClassTitle("");
      setStatusMsg("Class created.");
      await load();
    } else {
      setStatusMsg(`Failed to create class (${res.status}).`);
    }
  };

  return (
    <section className="academy-panel" aria-label="Institution & Class Policies">
      <h2>Institution &amp; Class Policies</h2>
      <p className="muted">
        Configure K-12, Higher Education, or Small-Business templates, privacy retention, and
        classroom isolation.
      </p>
      {statusMsg && <p className="academy-feedback">{statusMsg}</p>}

      {org && (
        <p className="muted">
          Retention: <strong>{org.retention_days} days</strong> · Daily token cap:{" "}
          <strong>{org.max_tokens_per_day_per_user}</strong> · Student agents:{" "}
          <strong>{org.allow_student_agents ? "enabled" : "disabled"}</strong>
          <br />
          Privacy notice: {org.privacy_notice}
        </p>
      )}

      <div className="toolbar">
        <label className="toolbar-field">
          <span>Organization Name</span>
          <input
            className="status-select"
            type="text"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
          />
        </label>
        <label className="toolbar-field">
          <span>Profile Template</span>
          <select
            className="status-select"
            value={template}
            onChange={(e) =>
              setTemplate(e.target.value as "k12_school" | "university" | "small_business")
            }
          >
            <option value="k12_school">K-12 School</option>
            <option value="university">Higher Education / Lab</option>
            <option value="small_business">Small Business / Team</option>
          </select>
        </label>
        <button type="button" className="btn" onClick={() => void saveOrg()}>
          Save Institution Policy
        </button>
      </div>

      <div className="toolbar">
        <label className="toolbar-field">
          <span>New Class Title</span>
          <input
            className="status-select"
            type="text"
            placeholder="e.g. Intro to AI 101"
            value={classTitle}
            onChange={(e) => setClassTitle(e.target.value)}
          />
        </label>
        <label className="toolbar-field">
          <span>Model Guidance</span>
          <input
            className="status-select"
            type="text"
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
          />
        </label>
        <button type="button" className="btn" onClick={() => void createClass()}>
          Create Class
        </button>
      </div>

      {classes.length > 0 && (
        <ul>
          {classes.map((cls) => (
            <li key={cls.class_id}>
              <strong>{cls.title}</strong> (<code>{cls.class_id}</code>) —{" "}
              {cls.model_guidance || "No guidance"}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
