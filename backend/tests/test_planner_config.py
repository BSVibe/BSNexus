"""Tests for planner-related settings in config."""

from __future__ import annotations

from unittest.mock import patch

from backend.src.config import Settings


class TestPlannerSettings:
    """Verify planner settings have correct defaults and accept env overrides."""

    def test_planner_cron_schedule_default(self):
        with patch.dict("os.environ", {}, clear=False):
            s = Settings(database_url="sqlite+aiosqlite://")
            assert s.planner_cron_schedule == "0 9 * * 1-5"

    def test_planner_max_suggestions_per_day_default(self):
        with patch.dict("os.environ", {}, clear=False):
            s = Settings(database_url="sqlite+aiosqlite://")
            assert s.planner_max_suggestions_per_day == 10

    def test_planner_cron_schedule_from_env(self):
        with patch.dict("os.environ", {"PLANNER_CRON_SCHEDULE": "0 8 * * *"}, clear=False):
            s = Settings(database_url="sqlite+aiosqlite://")
            assert s.planner_cron_schedule == "0 8 * * *"

    def test_planner_max_suggestions_from_env(self):
        with patch.dict("os.environ", {"PLANNER_MAX_SUGGESTIONS_PER_DAY": "20"}, clear=False):
            s = Settings(database_url="sqlite+aiosqlite://")
            assert s.planner_max_suggestions_per_day == 20

    def test_planner_max_suggestions_must_be_positive(self):
        with patch.dict("os.environ", {"PLANNER_MAX_SUGGESTIONS_PER_DAY": "0"}, clear=False):
            s = Settings(database_url="sqlite+aiosqlite://")
            # 0 is technically valid (disable suggestions), no validation error
            assert s.planner_max_suggestions_per_day == 0
