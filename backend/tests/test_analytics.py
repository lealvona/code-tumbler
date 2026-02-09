"""Tests for the analytics endpoints."""

import pytest


@pytest.mark.asyncio
async def test_global_stats(client):
    resp = await client.get("/api/analytics/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "project_count" in data
    assert "total_cost" in data
    assert "total_tokens" in data


@pytest.mark.asyncio
async def test_cost_timeseries(client):
    resp = await client.get("/api/analytics/cost-timeseries")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_cost_by_provider(client):
    resp = await client.get("/api/analytics/cost-by-provider")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_cost_per_iteration(client):
    resp = await client.get("/api/analytics/cost-per-iteration?project=nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
