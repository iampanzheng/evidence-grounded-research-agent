"""Public demo tests: synthetic evidence only; no third-party corpus or provider."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from p2_research.infrastructure.serving.api import APISettings, create_app
from tools.public_demo_api import load_public_demo_service, load_synthetic_fixture


def test_fixture_validates_source_and_hashes() -> None:
    chunks = load_synthetic_fixture()
    assert len(chunks) == 4
    assert all(chunk["source_url"] == "" for chunk in chunks)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What checks gate a fictional Orion release?", "ANSWERED"),
        ("How much revenue did Orion earn yesterday?", "INSUFFICIENT_EVIDENCE"),
    ],
)
def test_real_graph_serving_fake_model(query: str, expected: str) -> None:
    settings = APISettings(api_key="test-not-a-real-secret-value-00001", min_interval_seconds=0)
    with TestClient(create_app(settings, loader=load_public_demo_service)) as client:
        assert client.get("/health/ready").json()["status"] == "ready"
        assert client.post("/api/research", json={"question": query}).status_code == 401
        response = client.post(
            "/api/research",
            json={"question": query, "request_id": "demo-case-001"},
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == expected
        assert payload["claim_support"] == "NOT_VERIFIED"
        if expected == "ANSWERED":
            assert "[E1]" in payload["answer"]
            assert payload["citations"]
        else:
            assert payload["citations"] == []


def test_no_private_fingerprint_in_fixture() -> None:
    rendered = json.dumps(load_synthetic_fixture())
    assert "dubbo" not in rendered.lower()
    assert "a312e11036c2a7d6" not in rendered
