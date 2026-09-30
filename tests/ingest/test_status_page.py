from __future__ import annotations

from tests.conftest import post_fixture


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_status_page_before_and_after_push(client):
    empty = client.get("/")
    assert empty.status_code == 200
    assert "No push received yet" in empty.text
    assert "<script" not in empty.text

    post_fixture(client, "workouts_v2_recovery_only.json")
    bad = post_fixture(client, "malformed.json")
    assert bad.status_code == 422
    page = client.get("/").text
    assert "Last push:" in page
    assert "EDT" in page or "EST" in page
    assert "A1B2C3D4-0003-4000-8000-000000000003" in page
    assert "malformed" in page
    assert "<td>raw_archive</td><td>2</td>" in page
    assert "<td>activities</td><td>1</td>" in page
    assert "http" not in page.split("<body>")[1].split("Last 5")[0]
