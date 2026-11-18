"""Unit tests for lab scenario runners."""

from __future__ import annotations

from app.services.lab_scenarios import list_abilities, retrieve, run_ability


def test_list_abilities_wave1() -> None:
    ids = [item["id"] for item in list_abilities()]
    assert ids == ["tools", "rag", "structured", "guided", "generation"]


def test_retrieve_prefers_security_doc() -> None:
    hits = retrieve("Does Yonder authenticate users with RequestPrincipal?")
    assert hits
    assert any("security" in doc.id or "Security" in doc.title for doc in hits)


def test_tools_scenario_mentions_city() -> None:
    result = run_ability("tools", "Weather please in Denver")
    assert "Denver" in result["answer"]
    assert result["steps"][1]["type"] == "tool_call"


def test_generation_image_returns_placeholder_asset() -> None:
    result = run_ability("generation", "neon skyline", media_kind="image")
    assert result["assets"][0]["kind"] == "image"
    assert "training placeholder" in result["assets"][0]["note"].lower()
