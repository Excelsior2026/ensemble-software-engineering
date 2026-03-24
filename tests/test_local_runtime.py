from __future__ import annotations

from ese.local_runtime import ensure_local_runtime_ready


def test_ensure_local_runtime_ready_caches_successful_checks(monkeypatch) -> None:
    cfg = {
        "provider": {
            "name": "local",
            "model": "qwen2.5-coder:14b",
        },
        "roles": {
            "architect": {},
        },
        "runtime": {
            "adapter": "local",
            "local": {
                "base_url": "http://localhost:11434/v1",
            },
        },
    }
    probes: list[str] = []
    model_reads: list[str] = []

    def _fake_running(base_url: str, *, timeout_seconds: float = 2.0) -> bool:
        probes.append(base_url)
        return True

    def _fake_models(base_url: str) -> set[str]:
        model_reads.append(base_url)
        return {"qwen2.5-coder:14b"}

    monkeypatch.setattr("ese.local_runtime.ollama_running", _fake_running)
    monkeypatch.setattr("ese.local_runtime.fetch_ollama_models", _fake_models)

    ensure_local_runtime_ready(cfg)
    ensure_local_runtime_ready(cfg)

    assert probes == ["http://localhost:11434/v1"]
    assert model_reads == ["http://localhost:11434/v1"]
