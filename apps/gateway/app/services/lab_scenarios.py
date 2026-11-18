"""Training Lab scenarios — deterministic mock demos for teaching local AI patterns."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from app.paths import resolve_repo_root

AbilityId = Literal["tools", "rag", "structured", "guided", "generation"]
GuidedMode = Literal["summarize", "critique", "explain", "coach"]
MediaKind = Literal["image", "video"]


@dataclass(frozen=True)
class LabDocument:
    id: str
    title: str
    text: str


def corpus_path() -> Path:
    return resolve_repo_root() / "config" / "lab" / "corpus.yaml"


def load_corpus() -> list[LabDocument]:
    path = corpus_path()
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    docs = []
    for item in raw.get("documents", []):
        docs.append(
            LabDocument(
                id=str(item["id"]),
                title=str(item["title"]),
                text=" ".join(str(item["text"]).split()),
            )
        )
    return docs


def _score(query: str, doc: LabDocument) -> int:
    tokens = {t.lower() for t in query.replace(",", " ").split() if len(t) > 2}
    hay = f"{doc.title} {doc.text}".lower()
    return sum(1 for token in tokens if token in hay)


def retrieve(query: str, *, k: int = 3) -> list[LabDocument]:
    ranked = sorted(load_corpus(), key=lambda doc: _score(query, doc), reverse=True)
    hits = [doc for doc in ranked if _score(query, doc) > 0][:k]
    if hits:
        return hits
    # Fail closed to known corpus rather than inventing: return top architecture docs.
    return load_corpus()[:k]


WEATHER_TOOL = {
    "name": "lookup_weather",
    "description": "Return a scripted weather observation for a city (training only).",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}

RUBRIC_SCHEMA = {
    "$id": "yonder.lab.rubric.v1",
    "type": "object",
    "required": ["title", "score", "criteria", "pass"],
    "properties": {
        "title": {"type": "string"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "met", "note"],
                "properties": {
                    "name": {"type": "string"},
                    "met": {"type": "boolean"},
                    "note": {"type": "string"},
                },
            },
        },
        "pass": {"type": "boolean"},
    },
    "additionalProperties": False,
}


GUIDED_PROMPTS: dict[GuidedMode, str] = {
    "summarize": (
        "You are a concise summarizer. Capture the thesis and three bullets. "
        "Do not invent facts beyond the user text."
    ),
    "critique": (
        "You are a rigorous reviewer. List strengths, risks, and one concrete fix. "
        "Stay grounded in the submitted text."
    ),
    "explain": (
        "You are a patient teacher. Explain the idea in plain language, then give a "
        "short analogy. Avoid jargon unless you define it."
    ),
    "coach": (
        "You are a practice coach. Give next steps the trainee can try today. "
        "Keep advice actionable and kind."
    ),
}


def run_tools_scenario(user_input: str) -> dict[str, Any]:
    from yonder_contracts.tools import ToolCall

    from app.services.tools import build_default_tool_executor

    city = "Austin"
    lowered = user_input.lower()
    for candidate in ("austin", "seattle", "boston", "denver", "tokyo"):
        if candidate in lowered:
            city = candidate.title()
            break

    tool_args = {"city": city}
    executor = build_default_tool_executor()
    weather = executor.get("lookup_weather")
    assert weather is not None
    executed = executor.execute(
        ToolCall(id="lab-weather-1", name="lookup_weather", arguments=tool_args)
    )
    import json

    try:
        tool_result = json.loads(executed.content)
    except json.JSONDecodeError:
        tool_result = {"raw": executed.content}
    answer = (
        f"In {city} it is {tool_result.get('condition', 'unknown')} at "
        f"{tool_result.get('temp_c', '?')}°C "
        f"(training tool result via Gateway ToolExecutor). "
        "Tool use grounded this answer instead of guessing."
    )
    return {
        "ability": "tools",
        "mode": "training_scenario",
        "provider": "mock",
        "steps": [
            {
                "type": "plan",
                "title": "Decide whether a tool is needed",
                "detail": (
                    "Live facts (weather, inventory, tickets) should come from tools, "
                    "not model memory."
                ),
            },
            {
                "type": "tool_call",
                "name": weather.definition.name,
                "arguments": tool_args,
                "tool": {
                    "name": weather.definition.name,
                    "description": weather.definition.description,
                    "parameters": weather.definition.parameters_schema,
                },
            },
            {
                "type": "tool_result",
                "name": weather.definition.name,
                "result": tool_result,
            },
            {
                "type": "answer",
                "content": answer,
            },
        ],
        "answer": answer,
        "citations": [],
        "structured": None,
        "teaching": {
            "how_it_works": [
                "Model plans whether external data is required.",
                "Model emits a structured tool_call (name + JSON arguments).",
                "Gateway ToolExecutor runs allowlisted tools and returns a tool_result.",
                "Model writes the final answer grounded in that result.",
            ],
            "how_to_build": [
                "Register tools in app.services.tools (JSON schema + handler).",
                (
                    "Extend ChatRequest/ChatChunk contracts for tool_calls "
                    "when a real runtime supports them."
                ),
                (
                    "Keep tool execution in the gateway/worker boundary — "
                    "console never calls tools directly."
                ),
                "Log each step for teaching and audit; never invent tool payloads.",
            ],
        },
    }


def run_rag_scenario(user_input: str) -> dict[str, Any]:
    chunks = retrieve(user_input, k=3)
    citations = [
        {
            "id": doc.id,
            "title": doc.title,
            "snippet": doc.text[:220] + ("…" if len(doc.text) > 220 else ""),
        }
        for doc in chunks
    ]
    context_block = "\n\n".join(
        f"[{doc.id}] {doc.title}: {doc.text}" for doc in chunks
    )
    answer = (
        "Based on the retrieved Yonder lab corpus: "
        + " ".join(
            f"({c['id']}) {c['title']}" for c in citations
        )
        + ". "
        + (
            chunks[0].text
            if chunks
            else "No corpus hits; refuse rather than invent."
        )
    )
    return {
        "ability": "rag",
        "mode": "training_scenario",
        "provider": "mock",
        "steps": [
            {
                "type": "retrieve",
                "title": "Retrieve top chunks",
                "query": user_input,
                "chunks": citations,
            },
            {
                "type": "augment",
                "title": "Augment the prompt",
                "detail": "Inject retrieved text as grounded context before generation.",
                "context_preview": context_block[:600],
            },
            {
                "type": "answer",
                "content": answer,
            },
        ],
        "answer": answer,
        "citations": citations,
        "structured": None,
        "teaching": {
            "how_it_works": [
                "Index trusted documents into chunks (here: config/lab/corpus.yaml).",
                "Retrieve the most relevant chunks for the question.",
                "Augment the model prompt with those chunks.",
                "Generate an answer that cites sources and stays inside the context.",
            ],
            "how_to_build": [
                "Keep the corpus in reviewable config — not hard-coded secrets.",
                "Expose citations in the API so the console can teach verification.",
                "Route RAG through the Gateway; workers may embed/retrieve later.",
                "When context is empty, fail closed instead of hallucinating.",
            ],
        },
    }


def run_structured_scenario(user_input: str) -> dict[str, Any]:
    import json

    from app.services.structured import (
        build_default_schema_registry,
        validate_structured_content,
    )

    lowered = user_input.lower()
    score = 78 if "good" in lowered or "solid" in lowered else 62
    passed = score >= 70
    data = {
        "title": "Training submission rubric",
        "score": score,
        "criteria": [
            {
                "name": "Grounded in facts",
                "met": True,
                "note": "Claims reference supplied material.",
            },
            {
                "name": "Actionable next step",
                "met": "next" in lowered or "step" in lowered or passed,
                "note": "Includes at least one concrete follow-up.",
            },
            {
                "name": "No invented metrics",
                "met": "gpu" not in lowered or "not_reported" in lowered,
                "note": "Avoids fabricating hardware numbers.",
            },
        ],
        "pass": passed,
    }
    registry = build_default_schema_registry()
    entry = registry.get("yonder.lab.rubric.v1")
    assert entry is not None
    validation = validate_structured_content(
        json.dumps(data),
        schema=entry.json_schema,
        schema_id=entry.schema_id,
    )
    answer = (
        f"Structured rubric score {score}/100 — "
        f"{'pass' if passed else 'needs work'} (schema {entry.schema_id})."
    )
    return {
        "ability": "structured",
        "mode": "training_scenario",
        "provider": "mock",
        "steps": [
            {
                "type": "schema",
                "title": "Publish the JSON schema",
                "schema": entry.json_schema,
            },
            {
                "type": "structured",
                "title": "Model emits schema-valid JSON",
                "data": data,
            },
            {
                "type": "validate",
                "title": "Validate fail-closed",
                "valid": validation.valid,
                "errors": list(validation.errors),
                "detail": "Reject or retry if required fields are missing.",
            },
            {
                "type": "answer",
                "content": answer,
            },
        ],
        "answer": answer,
        "citations": [],
        "structured": {"schema_id": entry.schema_id, "data": data},
        "teaching": {
            "how_it_works": [
                "Define a JSON schema for the artifact you need (rubric, checklist, ticket).",
                "Ask the model to produce only that shape.",
                "Validate the payload with Gateway SchemaRegistry before the product consumes it.",
                "On validation failure, retry or fail closed — never silently coerce.",
            ],
            "how_to_build": [
                "Register schemas in app.services.structured.",
                "Use response_format / json_schema on the chat path when a runtime supports it.",
                "Surface validation errors to operators; do not invent missing fields.",
                "Keep Lab scenarios calling the same validator as product APIs.",
            ],
        },
    }


def run_guided_scenario(user_input: str, mode: GuidedMode) -> dict[str, Any]:
    system = GUIDED_PROMPTS[mode]
    preview = user_input.strip()[:280] or "(empty input)"
    if mode == "summarize":
        answer = (
            f"Summary ({mode}): Thesis — the trainee submitted material for practice. "
            f"Bullets: (1) Stay gateway-mediated. (2) Prefer grounded answers. "
            f"(3) Keep demos honest. Source excerpt: {preview}"
        )
    elif mode == "critique":
        answer = (
            f"Critique ({mode}): Strength — clear intent. Risk — vague success criteria. "
            f"Fix — add one measurable outcome. Source excerpt: {preview}"
        )
    elif mode == "explain":
        answer = (
            f"Explain ({mode}): In plain terms, this is practice text for a guided mode. "
            f"Analogy — like a coach handing you a playbook before the drill. "
            f"Source excerpt: {preview}"
        )
    else:
        answer = (
            f"Coach ({mode}): Next — rewrite one paragraph with a citation. "
            f"Then — run the RAG lab and compare grounding. "
            f"Source excerpt: {preview}"
        )
    return {
        "ability": "guided",
        "mode": "training_scenario",
        "provider": "mock",
        "guided_mode": mode,
        "steps": [
            {
                "type": "system_prompt",
                "title": f"Apply guided mode: {mode}",
                "content": system,
            },
            {
                "type": "answer",
                "content": answer,
            },
        ],
        "answer": answer,
        "citations": [],
        "structured": None,
        "teaching": {
            "how_it_works": [
                "A guided mode is a productized system prompt, not a different model.",
                "The same chat pipeline runs; only the instruction layer changes.",
                "Presets teach trainees when prompt design is the product surface.",
            ],
            "how_to_build": [
                "Store presets in config or Lab constants — keep them reviewable.",
                "Offer Open in Chat with the same system prompt for freeform follow-up.",
                "Still route through /v1/chat/completions or /v1/lab/run via the Gateway.",
            ],
        },
    }


def list_abilities() -> list[dict[str, Any]]:
    return [
        {
            "id": "tools",
            "title": "Tool use",
            "summary": "Plan → tool_call → tool_result → grounded answer.",
            "wave": 1,
        },
        {
            "id": "rag",
            "title": "RAG",
            "summary": "Retrieve trusted chunks, augment the prompt, cite sources.",
            "wave": 1,
        },
        {
            "id": "structured",
            "title": "Structured output",
            "summary": "Emit schema-valid JSON and validate fail-closed.",
            "wave": 1,
        },
        {
            "id": "guided",
            "title": "Guided modes",
            "summary": "Summarize, critique, explain, and coach via system prompts.",
            "wave": 1,
        },
        {
            "id": "generation",
            "title": "AI generation",
            "summary": "Image and video generation jobs through the Gateway.",
            "wave": 1,
        },
    ]


ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}

STYLE_PRESETS: dict[str, str] = {
    "yonder-brand": "Chrome Y mark, hex HUD, electric cyan rim light, void black",
    "photoreal": "Photoreal lighting, natural materials, shallow depth of field",
    "illustration": "Clean vector illustration, bold shapes, limited palette",
    "none": "",
}


def list_generation_presets() -> dict[str, Any]:
    return {
        "modes": ["txt2img", "img2img", "txt2video", "image2video"],
        "media_kinds": ["image", "video"],
        "aspect_ratios": list(ASPECT_SIZES.keys()),
        "style_presets": [
            {"id": key, "prompt_hint": value}
            for key, value in STYLE_PRESETS.items()
        ],
        "note": (
            "Studio controls are gateway-mediated. Until a generation worker exists, "
            "outputs are labeled training placeholders."
        ),
    }


def run_generation_scenario(
    user_input: str,
    *,
    media_kind: MediaKind = "image",
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = generation or {}
    mode = str(opts.get("mode") or "txt2img")
    if mode in {"txt2video", "image2video"}:
        media_kind = "video"
    aspect = str(opts.get("aspect_ratio") or ("16:9" if media_kind == "image" else "16:9"))
    width, height = ASPECT_SIZES.get(aspect, (1024, 576))
    negative = str(opts.get("negative_prompt") or "").strip()
    steps = int(opts.get("steps") or 20)
    guidance = float(opts.get("guidance") or 7.0)
    strength = opts.get("strength")
    style_id = str(opts.get("style_preset") or "none")
    style_hint = STYLE_PRESETS.get(style_id, "")
    reference_note = str(opts.get("reference_note") or "").strip()

    prompt = " ".join(user_input.split())
    if style_hint:
        prompt = f"{prompt}. Style: {style_hint}"
    short = prompt[:72] + ("…" if len(prompt) > 72 else "")
    seed = opts.get("seed")
    if seed is None:
        seed = sum(ord(ch) for ch in prompt) % 10_000
    else:
        seed = int(seed)
    job_id = f"lab-gen-{mode}-{seed:04d}"

    policy = {
        "status": "passed",
        "checks": ["prompt_length", "disallowed_token_stub", "worker_route_gateway_only"],
        "detail": "Training policy gate — replace with approved safety stack later.",
    }

    if media_kind == "image":
        asset = {
            "asset_id": f"{job_id}-frame",
            "kind": "image",
            "mime_type": "image/svg+xml",
            "width": width,
            "height": height,
            "aspect_ratio": aspect,
            "status": "ready",
            "source": "yonder-lab-mock-image",
            "note": "Training placeholder — not a real diffusion output.",
            "preview_url": _placeholder_image_data_url(short, seed, width=width, height=height),
        }
        answer = (
            f"Queued {mode} image job {job_id} ({aspect}, seed {seed}) for “{short}”. "
            "Training placeholder returned through the Gateway "
            "(not a live diffusion runtime)."
        )
        decode_detail = "VAE/decode step would materialize pixels on a real image worker."
    else:
        frames = [
            {
                "index": index,
                "timecode_s": index * 0.5,
                "preview_url": _placeholder_image_data_url(
                    f"{short} · t={index}",
                    seed + index,
                    width=width,
                    height=height,
                ),
            }
            for index in range(4)
        ]
        asset = {
            "asset_id": f"{job_id}-clip",
            "kind": "video",
            "mime_type": "video/mp4",
            "width": width,
            "height": height,
            "aspect_ratio": aspect,
            "fps": 24,
            "duration_s": 2.0,
            "status": "storyboard_ready",
            "source": "yonder-lab-mock-video",
            "note": (
                "Training storyboard — no binary video bytes until a video "
                "worker exists. Frames are explicit placeholders."
            ),
            "frames": frames,
            "preview_url": frames[0]["preview_url"],
        }
        answer = (
            f"Queued {mode} video job {job_id} ({aspect}, seed {seed}) for “{short}”. "
            "Returned a 4-frame storyboard placeholder through the Gateway "
            "(not a live video runtime)."
        )
        decode_detail = (
            "A real video worker would encode frames to MP4/WebM; "
            "this lab returns storyboard placeholders only."
        )

    steps_out: list[dict[str, Any]] = [
        {
            "type": "policy",
            "title": "Safety / policy check",
            "detail": policy["detail"],
            "result": policy,
        },
        {
            "type": "prompt",
            "title": "Normalize the creative brief",
            "detail": "Capture subject, style, aspect, negatives, and references before enqueue.",
            "content": prompt,
            "negative_prompt": negative or None,
            "style_preset": style_id,
            "mode": mode,
        },
    ]
    if mode in {"img2img", "image2video"}:
        steps_out.append(
            {
                "type": "reference",
                "title": "Attach reference (training stub)",
                "detail": reference_note
                or (
                    "Studio accepted a reference descriptor — "
                    "binary upload lands with real storage."
                ),
                "strength": strength,
            }
        )
    steps_out.extend(
        [
            {
                "type": "queue",
                "title": f"Enqueue {media_kind} job on Gateway",
                "job_id": job_id,
                "detail": (
                    "Clients never talk to GPU workers directly. "
                    "The Gateway owns admission, routing, and audit."
                ),
                "params": {
                    "steps": steps,
                    "guidance": guidance,
                    "aspect_ratio": aspect,
                    "seed": seed,
                    "strength": strength,
                },
            },
            {
                "type": "sample",
                "title": "Sample / denoise (training stub)",
                "detail": (
                    "On a real stack this is the diffusion/transformer step on a "
                    "generation worker. Here we only simulate progress."
                ),
                "progress": 1.0,
            },
            {
                "type": "decode",
                "title": "Decode / assemble asset",
                "detail": decode_detail,
            },
            {
                "type": "asset",
                "title": "Return gateway-mediated asset metadata",
                "asset": asset,
            },
            {
                "type": "answer",
                "content": answer,
            },
        ]
    )

    return {
        "ability": "generation",
        "mode": "training_scenario",
        "provider": "mock",
        "media_kind": media_kind,
        "steps": steps_out,
        "answer": answer,
        "citations": [],
        "structured": {
            "schema_id": "yonder.lab.generation_job.v1",
            "data": {
                "job_id": job_id,
                "media_kind": media_kind,
                "generation_mode": mode,
                "prompt": prompt,
                "negative_prompt": negative or None,
                "aspect_ratio": aspect,
                "seed": seed,
                "steps": steps,
                "guidance": guidance,
                "strength": strength,
                "style_preset": style_id,
                "status": asset["status"],
            },
        },
        "assets": [asset],
        "teaching": {
            "how_it_works": [
                "Treat generation as a job: policy → prompt → queue → sample → decode → asset.",
                (
                    "Studio controls (aspect, seed, negatives, img2img) are "
                    "product surface — same Gateway path."
                ),
                "Images and video share the control-plane shape; workers differ.",
                "Until a real generation worker exists, label placeholders as training stubs.",
            ],
            "how_to_build": [
                (
                    "Add a GenerationProvider adapter beside InferenceProvider — "
                    "do not call SD/FFmpeg from the console."
                ),
                "Store prompts, seeds, and job IDs in an auditable ledger.",
                "Serve assets via gateway URLs or signed object storage — never raw worker IPs.",
                "Keep safety/policy checks on the control plane before enqueue.",
            ],
        },
    }


def _placeholder_image_data_url(
    label: str,
    seed: int,
    *,
    width: int = 1024,
    height: int = 576,
) -> str:
    """Deterministic SVG placeholder — not a real model sample."""
    safe = (
        label.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    hue = 190 + (seed % 40)
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}'>"
        f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0%' stop-color='hsl({hue} 90% 18%)'/>"
        f"<stop offset='100%' stop-color='hsl({hue + 20} 80% 8%)'/>"
        f"</linearGradient></defs>"
        f"<rect width='{width}' height='{height}' fill='url(#g)'/>"
        f"<rect x='{int(width*0.05)}' y='{int(height*0.08)}' width='{int(width*0.9)}' "
        f"height='{int(height*0.84)}' fill='none' stroke='hsl({hue} 90% 60%)' "
        f"stroke-width='2' stroke-dasharray='10 8' opacity='0.7'/>"
        f"<text x='{width // 2}' y='{height // 2 - 20}' fill='#8ecfff' "
        f"font-family='ui-sans-serif,system-ui' font-size='28' text-anchor='middle'>"
        f"Yonder Studio · training placeholder</text>"
        f"<text x='{width // 2}' y='{height // 2 + 24}' fill='#d7e7ff' "
        f"font-family='ui-sans-serif,system-ui' font-size='18' text-anchor='middle'>{safe}</text>"
        f"<text x='{width // 2}' y='{height // 2 + 56}' fill='#5f7f9a' "
        f"font-family='ui-monospace,monospace' font-size='14' text-anchor='middle'>"
        f"seed {seed} · {width}x{height} · not live generation</text>"
        f"</svg>"
    )
    from urllib.parse import quote

    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def run_ability(
    ability: AbilityId,
    user_input: str,
    *,
    guided_mode: GuidedMode | None = None,
    media_kind: MediaKind | None = None,
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if ability == "tools":
        return run_tools_scenario(user_input)
    if ability == "rag":
        return run_rag_scenario(user_input)
    if ability == "structured":
        return run_structured_scenario(user_input)
    if ability == "guided":
        mode: GuidedMode = guided_mode or "summarize"
        return run_guided_scenario(user_input, mode)
    if ability == "generation":
        kind: MediaKind = media_kind or "image"
        if generation and generation.get("media_kind") in {"image", "video"}:
            kind = generation["media_kind"]  # type: ignore[assignment]
        return run_generation_scenario(
            user_input,
            media_kind=kind,
            generation=generation,
        )
    raise ValueError(f"Unknown ability: {ability}")
