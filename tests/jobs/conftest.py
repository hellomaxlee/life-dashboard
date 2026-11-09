from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import BackupConfig, ClaudeUsageConfig, Settings


@pytest.fixture
def jobs_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings whose usage file and backup dir are under tmp_path; scheduler still off."""
    return replace(
        settings,
        claude_usage=ClaudeUsageConfig(tmp_path / "claude_usage.json", 24),
        backup=BackupConfig(tmp_path / "backups", "03:15", 3),
    )


@pytest.fixture(autouse=True)
def user_at_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The month job asks pmset whether anyone is at the host; tests never depend on this
    Mac's power state. A test that wants the dark-wake case patches it back."""
    from app.jobs import scheduler

    monkeypatch.setattr(scheduler, "user_present", lambda: True)
