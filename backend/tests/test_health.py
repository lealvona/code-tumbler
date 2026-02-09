"""Tests for the health endpoint."""

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "database" in data


@pytest.mark.asyncio
async def test_health_database_field_is_string(client):
    resp = await client.get("/api/health")
    data = resp.json()
    assert isinstance(data["database"], str)
    assert data["database"] in ("connected", "unavailable")
