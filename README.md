# Nordic Energy — UK Tender Intelligence

A monorepo containing the FastAPI backend and React dashboard for Nordic Energy's internal tender intelligence platform. Aggregates UK public procurement notices from four government sources, scores them against Nordic Energy's service scope, and surfaces actionable opportunities in a live dashboard.

---

## Repository Structure

```
UK-tender-intelligence/
├── app/                          # FastAPI backend
│   ├── main.py                   # App entry point, CORS, startup hooks
│   ├── config.py                 # Settings loaded from .env
│   ├── dependencies.py           # Shared cache + HTTP client
│   ├── models/
│   │   └── tender.py             # Pydantic models (Tender, ProcurementRecord, etc.)
│   ├── routers/
│   │   ├── tenders.py            # /tenders endpoints
│   │   ├── sources.py            # /sources health + stats
│   │   ├── refresh.py            # /refresh manual trigger
│   │   ├── digest.py             # /digest Teams webhook
│   │   ├── export.py             # /export/csv download
│   │   ├── summarise.py          # /tenders/{id}/summarise AI analysis
│   │   ├── market.py             # /market market intelligence endpoints
│   │   └── shortlist.py          # /shortlist bid shortlisting + feedback
│   └── services/
│       ├── aggregator.py         # Concurrent fetch, dedup, score, cache write
│       ├── scorer.py             # Relevance scoring engine
│       ├── filtering.py          # Filter + sort helpers
│       ├── find_a_tender.py      # Find a Tender (FaT) OCDS client
│       ├── contracts_finder.py   # Contracts Finder OCDS client
│       ├── sell2wales.py         # Sell2Wales OCDS client
│       ├── public_contracts_scotland.py  # PCS OCDS client
│       ├── framework_tagger.py   # Framework / procurement route tagger
│       ├── competitor_tagger.py  # Competitor win detection
│       ├── market_awards.py      # CPV-matched awarded contract fetcher
│       ├── watchlist.py          # Watched authorities matcher
│       └── scheduler.py          # APScheduler daily refresh job
├── dashboard/                    # React + Vite frontend
│   ├── src/
│   │   └── App.jsx               # Single-file React dashboard
│   ├── public/
│   └── package.json
├── tests/
│   ├── test_scorer.py
│   ├── test_aggregator.py
│   └── test_api.py
├── shortlist_data.json           # Persisted shortlist + feedback (auto-created)
├── .env.example
├── requirements.txt
└── README.md
```

---

## Data Sources

| Source | Coverage | Format | Notes |
|--------|----------|--------|-------|
| **Find a Tender (FaT)** | England, Wales, NI (above threshold) | OCDS Release Packages | Procurement Act 2023 notice types (UK1–UK17). Three stage fetches: `planning`, `tender`, `award`. |
| **Contracts Finder (CF)** | England | OCDS Search (POST) | Below and above threshold. Planning, tender, award stages. |
| **Sell2Wales (S2W)** | Wales | OCDS (monthly) | 20 notice types. Monthly date ranges only. |
| **Public Contracts Scotland (PCS)** | Scotland | OCDS (monthly) | 9 notice types. SSL verification disabled on Windows (gov.uk cert chain issue). |

All sources return OCDS-format releases. Notices are deduplicated cross-source using Jaccard title similarity (threshold 0.85), with higher-priority notice types (UK4 active tender > UK1 pipeline) preferred when a match is found.

---

## Quick Start

### API (Backend)

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env as needed — defaults work out of the box

# 4. Start the dev server
uvicorn app.main:app --reload --port 8000

# 5. Trigger an initial data fetch
curl -X POST http://localhost:8000/refresh/sync

# 6. Browse the auto-generated API docs
# http://localhost:8000/docs
```

### Dashboard (Frontend)

```bash
cd dashboard
npm install
npm run dev
# Opens at http://localhost:5173
```

The dashboard connects to `http://localhost:8000` by default (`API_BASE` constant in `App.jsx`).

---

## API Endpoints

### Tenders

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenders` | List tenders with filters, scoring, and pagination |
| `GET` | `/tenders/{id}` | Single tender detail |
| `GET` | `/tenders/{id}/record` | Full procurement lifecycle (all OCID-linked notices) — FaT only |
| `POST` | `/tenders/{id}/summarise` | AI Go / No-go analysis via Ollama |
| `POST` | `/tenders/fetch/{notice_id}` | Fetch a specific FaT notice by ID and inject into cache |
| `POST` | `/tenders/fetch/s2w/{ocid}` | Fetch a specific Sell2Wales notice by OCID and inject into cache |

#### `GET /tenders` query parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `q` | — | Full-text search across title, authority, description |
| `source` | — | Filter by source name |
| `scope` | — | Filter by matched business scope label |
| `category` | — | Filter by procurement stage (`Opportunity`, `Future Opportunity`, `Early Engagement`, `Awarded Contract`) |
| `region` | — | Filter by NUTS delivery region prefix (e.g. `UKE` for Yorkshire, `UKD` for North West) |
| `cpv` | — | Filter by CPV code prefix (e.g. `71314` matches all energy services codes) |
| `min_score` | `5` | Minimum relevance score (0–10) |
| `sort_by` | `score` | `score` \| `deadline` \| `published` \| `value` |
| `sort_dir` | `desc` | `asc` \| `desc` |
| `page` | `1` | Page number |
| `page_size` | `25` | Results per page (max 10000) |

### Market Intelligence

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/market/status` | Check whether market award data has been loaded |
| `POST` | `/market/refresh` | Fetch CPV-matched awarded contracts from all sources (2–5 min) |
| `GET` | `/market/awards` | List CPV-matched awarded contracts with filters |

The market refresh queries all four sources for awarded contracts matching Nordic Energy's CPV codes over the past 30 days (FaT), 6 months (CF), and 12 months (S2W, PCS). Results are tagged with competitor wins where the awarded supplier matches a tracked competitor.

### Shortlist & Bid Feedback

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/shortlist` | List all shortlisted tenders with feedback |
| `POST` | `/shortlist/{id}` | Add a tender to the shortlist |
| `DELETE` | `/shortlist/{id}` | Remove a tender from the shortlist |
| `PUT` | `/shortlist/{id}/feedback` | Update bid assessment feedback |
| `GET` | `/shortlist/report` | Management review report with aggregated stats |
| `GET` | `/shortlist/export/csv` | Download shortlist with all feedback as CSV |

Shortlist data is persisted to `shortlist_data.json` in the project root and survives API restarts.

### Sources, Health & Utilities

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sources` | Per-source health, last fetch time, tender count |
| `GET` | `/health` | API health check |
| `POST` | `/refresh` | Trigger async background refresh |
| `POST` | `/refresh/sync` | Trigger synchronous refresh (blocks until complete) |
| `POST` | `/digest` | Send scored tenders to the configured Teams webhook |
| `GET` | `/export/csv` | Download filtered tenders as CSV |

---

## Procurement Stage Categories

Notices from all sources are normalised into four unified categories:

| Category | FaT Notice Types | CF / S2W / PCS Equivalent |
|----------|-----------------|--------------------------|
| **Opportunity** | UK4 (Tender), UK5 (Transparency) | Contract Notice, Invitation to Tender |
| **Future Opportunity** | UK1 (Pipeline), UK13–15 (Dynamic Markets) | Prior Information Notice, PIN |
| **Early Engagement** | UK2 (PME), UK3 (Planned Procurement) | Market Engagement notices |
| **Awarded Contract** | UK6 (Award), UK7 (Contract Details) | Contract Award Notice |

UK3 notices are flagged with urgency — the tendering window may be as short as 10 days once the UK4 drops.

---

## Relevance Scoring

Each tender is scored 0–10 against Nordic Energy's four core service areas:

| Service | Scope |
|---------|-------|
| **Service 01** | Renewable Energy Opportunity Identification |
| **Service 02** | Energy Feasibility Studies |
| **Service 03** | Energy System Optimisation |
| **Service 04** | Business Case Development |

### Scoring rules

- **Multi-word keyword match** in title or description → +2 points
- **Single-word keyword match** → +1 point
- **CPV code match** → +1 point
- **Hard negatives** (electricity supply contracts, waste, highways, catering, security, payroll, etc.) → score forced to **0**
- **Title-only keywords** (net zero, decarbonisation, ESG, carbon reduction, etc.) — only score from title; matching in description alone does not count
- **No title match** → score capped at **5** regardless of description matches
- **Score cap** → maximum 10

### Score labels

| Score | Label |
|-------|-------|
| 7–10 | Strong match |
| 4–6 | Likely relevant |
| 0–3 | Weak match |

The default API filter (`min_score=5`) excludes weak matches. The Teams digest only sends score ≥ 7 strong matches and score ≥ 6 high-likely notices.

---

## Competitor Tracking

The following competitors are tracked across awarded contract data:

| Competitor | Dashboard colour |
|------------|-----------------|
| Advanced Infrastructure | Blue |
| City Science | Green |
| Grid Edge | Orange |
| Tibo Energy | Purple |
| Centre for Sustainable Energy | Gold |
| Element Energy | Red |
| Regen | Teal |
| Living Places | Salmon |
| Vital Energi | Magenta |

Competitor wins are detected by matching the `awarded_supplier` field against each competitor's name (case-insensitive substring). They appear as tagged entries in both the **Competitor Activity** tab and the **Market Intelligence** tab.

---

## Environment Variables

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated allowed frontend origins |
| `CACHE_TTL_MINUTES` | `60` | In-memory cache TTL |
| `REFRESH_CRON` | `0 7 * * *` | APScheduler cron for daily refresh (7 AM UTC) |
| `MIN_SCORE_DEFAULT` | `5` | Default minimum relevance score |
| `FAT_BASE_URL` | `https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages` | FaT OCDS endpoint |
| `FAT_PAGE_SIZE` | `50` | Results per page from FaT |
| `FAT_API_KEY` | — | Optional FaT API key (raises rate limits) |
| `CF_BASE_URL` | `https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search` | CF OCDS search endpoint |
| `CF_PAGE_SIZE` | `100` | Results per page from CF |
| `TEAMS_WEBHOOK_URL` | — | Microsoft Teams incoming webhook for digest notifications |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint for AI tender summarisation |
| `OLLAMA_MODEL` | `llama3` | Model used for AI analysis |

---

## Resilience & Rate Limiting

- **FaT**: 1.5s proactive inter-page delay; exponential backoff (60→120→240s) on HTTP 429; short fixed 10s retry on DNS/network errors (max 2 attempts) to avoid blocking the refresh for minutes on a transient outage.
- **CF**: Exponential backoff on 429 with Retry-After header support.
- **All sources**: Concurrent fetch via `asyncio.gather` with a 10-minute per-source timeout. If a source exceeds the timeout or returns 0 results, the aggregator falls back to the previous cache for that source rather than serving an empty list.
- **Deduplication**: Jaccard similarity (threshold 0.85) across sources; UK4 (active tender) beats UK1 (pipeline) when they share an OCID.

---

## Dashboard

The dashboard is a single-page React app (`dashboard/src/App.jsx`) with a fixed three-column shell — only the centre column scrolls.

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ HEADER  Logo · Tenders N · Strong N · Likely N · Sources N · ...    │  ← always visible
├─────────────────┬──────────────────────────────┬─────────────────────┤
│  ⚡ Tenders     │                              │  Categories         │
│  🏢 Competitor  │   Scrollable content         │  (or detail panel)  │
│  📈 Market      │                              │                     │
│  ★  Shortlist   │                              │                     │
│                 │                              │                     │
│  [source links] │                              │                     │
└─────────────────┴──────────────────────────────┴─────────────────────┘
```

### Metrics header

Seven live counters are always visible in the header regardless of which tab is active or how far the list has been scrolled:

| Metric | Description |
|--------|-------------|
| Tenders | Total notices in cache |
| Strong | Score ≥ 7 |
| Likely | Score 4–6 |
| Sources | Active data sources |
| NE Eligible | Nordic Energy is registered on the framework |
| Watchlist | Matches a watched authority |
| Shortlisted | Tenders added to the shortlist |

### Left navigation

Four vertical tab buttons, each with a live count badge:

| Tab | Content |
|-----|---------|
| ⚡ **Tenders** | Active opportunities with filters, scoring, and detail panel |
| 🏢 **Competitor Activity** | Awarded contracts grouped by tracked competitor |
| 📈 **Market Intelligence** | CPV-matched awarded contracts across all sources |
| ★ **Shortlist** | Shortlisted tenders with bid assessment and management review |

### Right panel (Tenders tab)

Shows a vertical category filter (All / Opportunity / Future Opportunity / Early Engagement / Awarded Contract) and per-source counts when no tender is selected. Expands from 190 px to 400 px to show the full detail panel when a tender is selected, then collapses back on close.

### Tenders tab

- Filter bar: free-text search, source, scope, NUTS region, CPV code prefix, minimum score slider, and CSV export
- Each tender card shows: source badge, category, notice type (UK1–UK6), procurement route, watchlist flag, service scope tags, title, authority, value, deadline (with urgency indicator), relevance score, and a ☆ shortlist button
- **Detail panel** (right sidebar): full tender metadata, contact point, matched keywords, OCID procurement family, CPV codes, and procurement history timeline (FaT only)
- **AI analysis**: on-demand Go / No-go assessment via local Ollama — summary, recommendation, confidence, key requirements, and fit assessment
- **Bid Assessment**: appears in the detail panel once a tender is shortlisted (see Shortlist tab below)

### Competitor Activity tab

Lists awarded contracts where the winning supplier is a tracked competitor. Grouped and colour-coded by company name. Selecting a row opens a contact panel with the authority contact point for market engagement outreach.

### Market Intelligence tab

Shows CPV-matched awarded contracts fetched on demand from all four sources. Key capabilities:

- Triggered manually via "Load Market Data" (typically 2–5 minutes)
- Filters by source, scope, and competitor wins
- Stats row: total awards, competitor wins, entries with contact info, total value
- Competitor win chips showing win counts per company
- Detail panel with full award information and contact point

### Shortlist tab

Tenders can be shortlisted by clicking ☆ on any tender card or in the detail panel. The shortlist tab has two views:

**List view** — each shortlisted tender shows its bid decision, confidence, outcome, and a one-line team note. Clicking a row opens the detail panel with the bid assessment form inline. Tenders can be removed from the shortlist here.

**Management Review** — a structured table across all shortlisted tenders for pipeline oversight:

| KPI | Description |
|-----|-------------|
| Shortlisted | Total tenders under consideration |
| Go Decisions | Count of `Go` bid decisions |
| No-go | Count of `No-go` bid decisions |
| Bids Submitted | Count of submitted bids |
| AI Accuracy | Average AI score accuracy rating (1–5) across all assessments |

The review table shows one row per tender with decision badge, confidence, outcome, and an inline-editable management notes cell. The "Export CSV" button downloads all shortlist entries with feedback columns for offline reporting.

### Bid Assessment form

Appears in the detail panel once a tender is shortlisted. Fields:

| Field | Options |
|-------|---------|
| Bid Decision | Go · No-go · Under Review · Bid Submitted |
| Confidence | High · Medium · Low |
| Outcome | Pending · Won · Lost · Withdrawn |
| AI Score Accuracy | 1–5 (1 = AI was wrong, 5 = spot-on) |
| Team Notes | Free text — bid team observations, risks, resource notes |
| Management Notes | Free text — strategic rationale, approval comments |

All feedback is persisted server-side to `shortlist_data.json` and survives page reloads and API restarts. The AI Score Accuracy rating feeds a learning loop: ratings collected across all shortlisted tenders provide ground-truth data for auditing and tuning the relevance scorer in `app/services/scorer.py`.

---

## Production Deployment

The API is stateless except for `shortlist_data.json`; the in-memory tender cache resets on restart.

For production:

1. **Persist cache** — replace `InMemoryCache` in `app/dependencies.py` with Redis
2. **Persist shortlist** — move `shortlist_data.json` storage to a database or mounted volume
3. **Run with Gunicorn** — `gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app`
4. **Set CORS** — update `CORS_ORIGINS` to your deployed dashboard domain
5. **FaT API key** — register at find-tender.service.gov.uk to get a key and set `FAT_API_KEY` for higher rate limits
6. **Build the dashboard** — `cd dashboard && npm run build`, then serve `dist/` via a static host or the FastAPI `StaticFiles` mount
