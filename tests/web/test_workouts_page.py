"""GET /workouts shows the form and the overrides; POST /workouts adds or removes one,
recomputes, and redirects back. Everything shown is escaped; no scripts, no external assets."""

from __future__ import annotations

import json
from datetime import date, timedelta

from app.metrics import manual
from app.timeutil import local_day, now_utc

EXTERNAL = ("http://", "https://", "<script", "<link ", "@import", "url(")
NASTY = "<script>alert('x')</script> & 4 mile \"run\""
ESCAPED = "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; 4 mile &quot;run&quot;"


def today(settings) -> str:
    return local_day(now_utc(), settings.home_tz)


def day_row(db, day: str) -> dict:
    query = "SELECT metrics_json FROM daily_metrics WHERE day_local = ?"
    row = db.execute(query, (day,)).fetchone()
    return json.loads(row["metrics_json"])


def test_get_shows_the_form_the_nav_and_no_overrides(client, settings):
    page = client.get("/workouts")
    assert page.status_code == 200
    for marker in EXTERNAL:
        assert marker not in page.text, marker
    assert "<nav class='pages'>" in page.text and "<b>workouts</b>" in page.text
    assert "<form class='add' method='post' action='/workouts'>" in page.text
    assert f"name='date' value='{today(settings)}' max='{today(settings)}'" in page.text
    assert "No overrides." in page.text
    for path in ("/", "/preview", "/pixoo"):
        assert "<a href='/workouts'>workouts</a>" in client.get(path).text


def test_post_adds_recomputes_redirects_and_escapes_the_note(client, db, settings):
    day = (date.fromisoformat(today(settings)) - timedelta(days=1)).isoformat()
    response = client.post(
        "/workouts", data={"action": "add", "date": day, "note": NASTY}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/workouts"
    assert [row.note for row in manual.list_overrides(db)] == [NASTY]
    assert day_row(db, day)["quality_workout"] is True
    assert day_row(db, day)["manual_workout"] is True

    page = client.get("/workouts").text
    assert ESCAPED in page and NASTY not in page
    assert "<script" not in page
    assert f"<input type='hidden' name='date' value='{day}'>" in page
    assert "<button type='submit'>remove</button>" in page


def test_post_remove_takes_the_credit_away(client, db, settings):
    day = today(settings)
    client.post("/workouts", data={"action": "add", "date": day, "note": "4 mile run"})
    assert day_row(db, day)["quality_workout"] is True

    response = client.post(
        "/workouts", data={"action": "remove", "date": day}, follow_redirects=False
    )
    assert response.status_code == 303
    assert manual.list_overrides(db) == []
    assert day_row(db, day)["quality_workout"] is False
    assert day_row(db, day)["manual_workout"] is False
    assert "No overrides." in client.get("/workouts").text


def test_a_bad_or_future_date_is_refused_with_the_reason_escaped(client, db, settings):
    tomorrow = (date.fromisoformat(today(settings)) + timedelta(days=1)).isoformat()
    response = client.post("/workouts", data={"action": "add", "date": tomorrow, "note": "x"})
    assert response.status_code == 400
    assert "after today" in response.text and manual.list_overrides(db) == []

    response = client.post("/workouts", data={"action": "add", "date": "<b>soon</b>", "note": ""})
    assert response.status_code == 400
    assert "&lt;b&gt;soon&lt;/b&gt;" in response.text and "<b>soon</b>" not in response.text
    assert manual.list_overrides(db) == []
