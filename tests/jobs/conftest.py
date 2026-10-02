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
