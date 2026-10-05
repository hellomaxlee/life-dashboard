"""tools.month: show, dry-run, run, force. The model is a fake; `make_client` is replaced."""

from __future__ import annotations

import pytest

from app.month import generate, store
from tests.conftest import count
from tests.month.conftest import FakeClient, feature_object, feature_text, reply
from tools import month as cli

MONTH = "2026-10"


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient(reply(feature_text(MONTH)), reply(feature_text(MONTH, title="Slow Tides")))
    monkeypatch.setattr(generate, "make_client", lambda settings: client)
    return client


def test_run_prints_title_theme_source_and_spend_and_is_idempotent(db, fake, capsys):
    assert cli.main(["--month", MONTH]) == 0
    out = capsys.readouterr().out
    assert "2026-10  SMALL WOODS" in out
    assert "theme: A walk through a wood" in out
    assert "source: model (claude-" in out
    assert "2026-10 [generated] model calls: 1; usd 0.3096" in out
    assert "spend: " in out and "1 call(s), month-to-date $0.3096 of cap $3.00" in out

    assert cli.main(["--month", MONTH]) == 0
    assert "2026-10 [stored] model calls: 0; usd 0.0000" in capsys.readouterr().out
    assert len(fake.requests) == 1

    assert cli.main(["--month", MONTH, "--force"]) == 0
    assert "2026-10  SLOW TIDES" in capsys.readouterr().out
    assert len(fake.requests) == 2


def test_show_prints_the_stored_captions_and_calls_nothing(db, fake, capsys):
    assert cli.main(["--month", MONTH, "--show"]) == 1
    assert "2026-10: no stored feature" in capsys.readouterr().out

    store.save_feature(db, MONTH, feature_object(MONTH), "model", "some-model", "")
    assert cli.main(["--month", MONTH, "--show"]) == 0
    out = capsys.readouterr().out
    assert "source: model (some-model)" in out
    assert "palette: #ff4020 #20c0ff #ffd000" in out
    assert " 1  THE ASH" in out and "31  THE ELDER" in out
    assert "A juniper keeps its own slow calendar." in out
    assert fake.requests == []


def test_dry_run_prices_the_request_and_calls_nothing(db, fake, capsys):
    assert cli.main(["--month", MONTH, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "The month is October 2026" in out
    assert "max_tokens 32000" in out
    assert "worst case: $0.65" in out and "for a run of 2 calls" in out
    assert "call allowed" in out
    assert "2026-10: not stored; guard: an automatic run may call; api key: absent" in out
    assert fake.requests == []
    assert count(db, "model_spend") == count(db, "month_features") == 0


def test_a_failed_run_exits_one_and_a_bad_month_is_refused(db, settings, capsys):
    assert cli.main(["--month", MONTH]) == 1
    assert "2026-10 [failed] model calls: 0; usd 0.0000; no api key" in capsys.readouterr().out
    assert cli.main(["--month", "October"]) == 2
    assert "is not YYYY-MM" in capsys.readouterr().err


def test_the_default_month_is_the_home_timezone_month(db, fake, capsys, monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setattr(cli, "now_utc", lambda: datetime(2026, 11, 1, 3, 30, tzinfo=UTC))
    assert cli.main(["--show"]) == 1
    assert "2026-10: no stored feature" in capsys.readouterr().out
