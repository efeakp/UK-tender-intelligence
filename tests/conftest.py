"""
Shared pytest fixtures.

Mocks the APScheduler so tests do not try to start a background cron job.
Without this, the second TestClient context in the same session fails because
the module-level AsyncIOScheduler holds a reference to the first test's closed
event loop and raises RuntimeError("Event loop is closed") on startup.
"""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_scheduler():
    with patch("app.services.scheduler.start_scheduler"), \
         patch("app.services.scheduler.stop_scheduler"):
        yield
