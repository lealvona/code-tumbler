"""Tests for the projects endpoints."""

import pytest


@pytest.mark.asyncio
async def test_list_projects(client):
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_project(client, tmp_path, monkeypatch):
    # Point workspace to tmp dir so we don't pollute real projects
    monkeypatch.setattr(
        client._transport.app.state,  # type: ignore[union-attr]
        "backend_root",
        str(tmp_path),
    )

    resp = await client.post(
        "/api/projects",
        json={"name": "test-unit", "requirements": "Hello world script"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test-unit"
    assert data["status"] == "created"

    # Verify directory structure was created
    project_dir = tmp_path / "projects" / "test-unit"
    assert (project_dir / "01_input" / "requirements.txt").exists()
