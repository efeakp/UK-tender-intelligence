from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List


class Settings(BaseSettings):
    # ── Server ────────────────────────────────────────────────────────────────
    app_env:   str = "development"
    log_level: str = "INFO"

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_ttl_minutes: int = 60

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # Cron expression: minute hour day month day_of_week
    # 15 10 * * * = 10:15 UTC = 11:15 BST
    refresh_cron: str = "15 10 * * *"

    # ── Tender fetch window ───────────────────────────────────────────────────
    # How many days back to look for notices on each refresh.
    # Planning/Pipeline stage uses max(refresh_days_back, 60) for wider coverage.
    refresh_days_back: int = 30

    # ── Scoring ───────────────────────────────────────────────────────────────
    min_score_default: int = 3

    # ── Source 1: Find a Tender ───────────────────────────────────────────────
    # Public API — no key required. Key is optional but raises rate limits.
    # Page size 100 = maximum allowed by FaT API (do not reduce)
    fat_base_url: str = (
        "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
    )
    fat_page_size: int = 100   # FaT API max — do not reduce
    fat_api_key:   str | None = None

    # ── Source 2: Contracts Finder ────────────────────────────────────────────
    # Public API — no key required.
    # Page size 100 = maximum allowed by CF OCDS endpoint
    cf_base_url: str = (
        "https://www.contractsfinder.service.gov.uk"
        "/Published/Notices/OCDS/Search"
    )
    cf_page_size: int = 100   # CF API max — do not reduce

    # ── Source 3: Sell2Wales ──────────────────────────────────────────────────
    # Public API (Open Government Licence) — no key required.
    # Currently returning 403 — awaiting resolution with Sell2Wales support.
    # Base URL hardcoded in app/services/sell2wales.py

    # ── Source 4: Public Contracts Scotland ──────────────────────────────────
    # Public API — no key required.
    # SSL verification disabled in client (Windows certificate chain issue).
    # Base URL hardcoded in app/services/public_contracts_scotland.py

    # ── AI Summarisation (Ollama — local, free) ───────────────────────────────
    # Model: gemma3:4b running via Ollama at http://localhost:11434
    # No API key needed — runs entirely on your machine.
    # Start Ollama with: ollama serve
    # Model configured in app/routers/summarise.py (OLLAMA_MODEL = "gemma3:4b")

    # ── Daily Digest (Teams + email) ─────────────────────────────────────────
    # Runs at 08:00 UTC (09:00 BST) daily via scheduler.
    # Manual trigger: POST /digest/send

    # Teams: webhook URL from Bid Hub → Tenders channel → right-click → Workflows
    # → Search "webhook" → "Send webhook alerts to a channel" → copy URL
    teams_webhook_url: str | None = None

    # Email via Microsoft Graph API
    # Requires Azure AD app with Mail.Send application permission
    digest_email_to:     str | None = None   # comma-separated recipients
    digest_from_email:   str | None = None   # licensed M365 sender mailbox
    graph_client_id:     str | None = None
    graph_client_secret: str | None = None
    graph_tenant_id:     str | None = None

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()