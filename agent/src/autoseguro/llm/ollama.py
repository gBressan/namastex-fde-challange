"""Consultas à API nativa do Ollama: o modelo está baixado? Está carregado na GPU?"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from autoseguro.config import Settings


@dataclass(frozen=True)
class OllamaStatus:
    model: str
    downloaded: bool
    loaded: bool
    # Fração do modelo carregado que está na VRAM (1.0 = 100% GPU; 0.0 = só CPU).
    gpu_fraction: float | None


def _root_url(settings: Settings) -> str:
    return settings.ollama_base_url.rstrip("/").removesuffix("/v1")


def ollama_status(settings: Settings, *, timeout: float = 3.0) -> OllamaStatus:
    """Lança `httpx.HTTPError` se o Ollama não responder."""
    root = _root_url(settings)
    model = settings.llm_model
    tags = httpx.get(f"{root}/api/tags", timeout=timeout).raise_for_status().json()
    downloaded = any(m.get("name") == model for m in tags.get("models", []))

    running = httpx.get(f"{root}/api/ps", timeout=timeout).raise_for_status().json()
    entry = next((m for m in running.get("models", []) if m.get("name") == model), None)
    gpu_fraction = None
    if entry and entry.get("size"):
        gpu_fraction = entry.get("size_vram", 0) / entry["size"]
    return OllamaStatus(model, downloaded, entry is not None, gpu_fraction)
