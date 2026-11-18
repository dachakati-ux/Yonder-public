"""Principal-scoped Academy practice sandboxes (RAG + parts ledger)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.curriculum import SandboxQuotas


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SandboxDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    text: str
    created_at: str


class PartsLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    part_number: str
    quantity: int = Field(ge=1, le=1000)
    incident_id: str
    note: str = ""
    created_at: str


class PracticeSandbox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    principal_subject: str
    label: str
    preset: str | None = None
    documents: list[SandboxDocument] = Field(default_factory=list)
    parts: list[PartsLedgerEntry] = Field(default_factory=list)
    retrieve_runs: int = 0
    created_at: str
    updated_at: str


class SandboxQuotaError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


SITE_OPS_FIXTURE_DOCS: list[dict[str, str]] = [
    {
        "id": "fixture-sel-host-a",
        "title": "Fixture SEL — lab-host-a",
        "text": (
            "SEL excerpt for lab-host-a (sanitized teaching fixture).\n"
            "2026-07-10T03:11:02Z | critical | Memory | Uncorrectable ECC on DIMM A1\n"
            "2026-07-10T03:11:05Z | warning | Memory | Correctable ECC threshold exceeded\n"
            "2026-07-10T04:02:18Z | ok | System | Host reboot complete\n"
            "Do not invent additional SEL lines. FRU likely: DIMM A1."
        ),
    },
    {
        "id": "fixture-health-host-a",
        "title": "Fixture health — lab-host-a",
        "text": (
            "Health sweep fixture for lab-host-a.\n"
            "Disks: SMART OK on bay0; bay1 not_reported.\n"
            "PCIe: link up x16 on slot3.\n"
            "GPU: section not_reported (collector did not return GPU block).\n"
            "Memory: ECC errors observed — see SEL.\n"
            "Mark GPU as unknown when the section is not_reported."
        ),
    },
    {
        "id": "fixture-inventory-host-a",
        "title": "Fixture inventory — lab-host-a",
        "text": (
            "Inventory teaching record for lab-host-a.\n"
            "Chassis: teaching-sku-1\n"
            "CPU: 2x teaching-cpu\n"
            "Memory: 8x 32GiB DIMM (slot A1 listed)\n"
            "NIC: not_reported\n"
            "Disks: bay0 1.92TB SSD serial TEACH-DISK-0001; bay1 not_reported\n"
            "Never invent NIC MACs or missing serials."
        ),
    },
    {
        "id": "fixture-runbook-sel",
        "title": "Runbook — SEL memory ECC",
        "text": (
            "Teaching runbook: Uncorrectable ECC on a DIMM.\n"
            "1) Confirm SEL evidence and DIMM location.\n"
            "2) Open incident with host id and evidence refs.\n"
            "3) If still under warranty per docs, prepare RMA packet for the DIMM.\n"
            "4) Do not invent warranty end dates.\n"
            "Change windows for non-emergency DIMM replacement: weekday 10:00-16:00 local lab time."
        ),
    },
    {
        "id": "fixture-rma-policy",
        "title": "Teaching RMA policy",
        "text": (
            "Teaching-only RMA notes (not a vendor contract).\n"
            "Required packet fields: part_number, failure_summary, evidence_refs, host_id.\n"
            "warranty_end is optional; use not_reported when unknown.\n"
            "Academy never calls real vendor APIs."
        ),
    },
]


def _load_corpus_docs(filename: str) -> list[dict[str, str]]:

    import yaml

    from app.paths import resolve_repo_root

    path = resolve_repo_root() / "config" / "lab" / filename
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    docs = []
    for item in raw.get("documents") or []:
        docs.append(
            {
                "id": str(item["id"]),
                "title": str(item["title"]),
                "text": str(item["text"]).strip(),
            }
        )
    return docs


PRESET_DOCUMENTS: dict[str, list[dict[str, str]]] = {
    "site-ops-fixture": SITE_OPS_FIXTURE_DOCS,
    "support-fixture": _load_corpus_docs("corpus-support.yaml"),
    "ops-fixture": _load_corpus_docs("corpus-ops.yaml"),
    "eng-fixture": _load_corpus_docs("corpus-eng.yaml"),
}

PRESET_LABELS: dict[str, str] = {
    "site-ops-fixture": "Site Ops Practice desk",
    "support-fixture": "Support Practice desk",
    "ops-fixture": "Ops Practice desk",
    "eng-fixture": "Engineering Practice desk",
}


class InMemorySandboxStore:
    def __init__(self, quotas: SandboxQuotas | None = None) -> None:
        self._sandboxes: dict[str, PracticeSandbox] = {}
        self.quotas = quotas or SandboxQuotas()

    def _owned(self, sandbox_id: str, principal_subject: str) -> PracticeSandbox | None:
        box = self._sandboxes.get(sandbox_id)
        if box is None or box.principal_subject != principal_subject:
            return None
        return box

    def list_for_principal(self, principal_subject: str) -> list[PracticeSandbox]:
        return sorted(
            (b for b in self._sandboxes.values() if b.principal_subject == principal_subject),
            key=lambda b: b.created_at,
            reverse=True,
        )

    def create(
        self,
        *,
        principal_subject: str,
        label: str = "Practice desk",
        preset: str | None = None,
    ) -> PracticeSandbox:
        existing = self.list_for_principal(principal_subject)
        if len(existing) >= self.quotas.max_sandboxes_per_principal:
            raise SandboxQuotaError(
                f"Sandbox quota exceeded (max {self.quotas.max_sandboxes_per_principal})."
            )
        now = _now()
        default_label = PRESET_LABELS.get(preset or "", label.strip() or "Practice desk")
        box = PracticeSandbox(
            id=f"sbx_{uuid.uuid4().hex[:12]}",
            principal_subject=principal_subject,
            label=default_label if preset else (label.strip() or "Practice desk"),
            preset=preset,
            created_at=now,
            updated_at=now,
        )
        preset_docs = PRESET_DOCUMENTS.get(preset or "") if preset else None
        if preset_docs:
            box.documents = [
                SandboxDocument(
                    id=doc["id"],
                    title=doc["title"],
                    text=doc["text"],
                    created_at=now,
                )
                for doc in preset_docs
            ]
        self._sandboxes[box.id] = box
        return box

    def get(self, sandbox_id: str, principal_subject: str) -> PracticeSandbox | None:
        return self._owned(sandbox_id, principal_subject)

    def reset(self, sandbox_id: str, principal_subject: str) -> PracticeSandbox | None:
        box = self._owned(sandbox_id, principal_subject)
        if box is None:
            return None
        preset = box.preset
        label = box.label
        del self._sandboxes[sandbox_id]
        return self.create(
            principal_subject=principal_subject, label=label, preset=preset
        )

    def delete(self, sandbox_id: str, principal_subject: str) -> bool:
        box = self._owned(sandbox_id, principal_subject)
        if box is None:
            return False
        del self._sandboxes[sandbox_id]
        return True

    def add_document(
        self,
        sandbox_id: str,
        principal_subject: str,
        *,
        title: str,
        text: str,
    ) -> SandboxDocument:
        box = self._owned(sandbox_id, principal_subject)
        if box is None:
            raise KeyError("sandbox_not_found")
        if len(box.documents) >= self.quotas.max_documents_per_sandbox:
            raise SandboxQuotaError(
                f"Document quota exceeded (max {self.quotas.max_documents_per_sandbox})."
            )
        if len(text) > self.quotas.max_chars_per_document:
            raise SandboxQuotaError(
                f"Document exceeds max_chars_per_document ({self.quotas.max_chars_per_document})."
            )
        total_chars = sum(len(d.text) for d in box.documents) + len(text)
        if total_chars > self.quotas.max_chars_per_sandbox:
            raise SandboxQuotaError(
                f"Sandbox char quota exceeded (max {self.quotas.max_chars_per_sandbox})."
            )
        doc = SandboxDocument(
            id=f"doc_{uuid.uuid4().hex[:10]}",
            title=title.strip() or "Untitled",
            text=text,
            created_at=_now(),
        )
        box.documents.append(doc)
        box.updated_at = _now()
        return doc

    def retrieve(
        self, sandbox_id: str, principal_subject: str, *, query: str, k: int = 3
    ) -> dict[str, Any]:
        box = self._owned(sandbox_id, principal_subject)
        if box is None:
            raise KeyError("sandbox_not_found")
        if box.retrieve_runs >= self.quotas.max_retrieve_runs_per_sandbox:
            raise SandboxQuotaError(
                f"Retrieve run quota exceeded (max {self.quotas.max_retrieve_runs_per_sandbox})."
            )
        box.retrieve_runs += 1
        box.updated_at = _now()
        tokens = {t.lower() for t in re.split(r"[^\w]+", query) if len(t) > 2}
        scored: list[tuple[float, SandboxDocument]] = []
        for doc in box.documents:
            hay = f"{doc.title} {doc.text}".lower()
            score = float(sum(1 for token in tokens if token in hay))
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits = []
        for score, doc in scored[: max(1, min(k, 8))]:
            hits.append(
                {
                    "document_id": doc.id,
                    "title": doc.title,
                    "score": score,
                    "snippet": doc.text[:400],
                    "citation": {"document_id": doc.id, "title": doc.title},
                }
            )
        return {
            "sandbox_id": box.id,
            "query": query,
            "hits": hits,
            "retrieve_runs": box.retrieve_runs,
            "note": "Sandbox RAG is isolated from the shared teaching corpus.",
        }

    def add_part(
        self,
        sandbox_id: str,
        principal_subject: str,
        *,
        part_number: str,
        quantity: int,
        incident_id: str,
        note: str = "",
    ) -> PartsLedgerEntry:
        box = self._owned(sandbox_id, principal_subject)
        if box is None:
            raise KeyError("sandbox_not_found")
        if len(box.parts) >= self.quotas.max_parts_entries_per_sandbox:
            raise SandboxQuotaError(
                f"Parts ledger quota exceeded (max {self.quotas.max_parts_entries_per_sandbox})."
            )
        entry = PartsLedgerEntry(
            id=f"part_{uuid.uuid4().hex[:10]}",
            part_number=part_number.strip(),
            quantity=quantity,
            incident_id=incident_id.strip(),
            note=note.strip(),
            created_at=_now(),
        )
        box.parts.append(entry)
        box.updated_at = _now()
        return entry
