"""Shared modality capability labels and availability states."""

from __future__ import annotations

from enum import StrEnum


class ModalityCapability(StrEnum):
    CHAT = "chat"
    STREAMING = "streaming"
    TOOLS = "tools"
    STRUCTURED = "structured"
    EMBEDDINGS = "embeddings"
    RERANKING = "reranking"
    IMAGE = "image"
    VIDEO = "video"
    RAG = "rag"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"


class CapabilityAvailability(StrEnum):
    """Honest availability — never invent readiness."""

    READY = "ready"
    MOCK = "mock"
    NOT_SUPPORTED = "not_supported"
    NOT_CONFIGURED = "not_configured"
    NOT_REPORTED = "not_reported"
