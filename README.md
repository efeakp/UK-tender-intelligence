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
│   │   └── export.py             # /export/csv download
│   └── services/
│       ├── aggregator.py         # Concurrent fetch, dedup, score, cache write
│       ├── scorer.py             # Relevance scoring engine
│       ├── filtering.py          # Filter + sort helpers
│       ├── find_a_tender.py      # Find a Tender (FaT) OCDS client
│       ├── contracts_finder.py   # Contracts Finder OCDS client
│       ├── sell2wales.py         # Sell2Wales OCDS client
│       ├── public_contracts_scotland.py  # PCS OCDS client
│       ├── framework_tagger.py   # Framework / procurement route tagger
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
| `min_score` | `5` | Minimum relevance score (0–10) |
| `sort_by` | `score` | `score` \| `deadline` \| `published` \| `value` |
| `sort_dir` | `desc` | `asc` \| `desc` |
| `page` | `1` | Page number |
| `page_size` | `25` | Results per page (max 2000) |

### Sources & Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sources` | Per-source health, last fetch time, tender count |
| `GET` | `/health` | API health check |

### Refresh

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/refresh` | Trigger async background refresh |
| `POST` | `/refresh/sync` | Trigger synchronous refresh (blocks until complete) |

### Digest & Export

| Method | Path | Description |
|--------|------|-------------|
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

## Dashboard Features

- Live tender list with score, category, source, notice type, and deadline urgency indicators
- Filter by source, business scope, procurement category, NUTS delivery region, and minimum score
- Click any tender to open a detail panel with description, matched keywords, CPV codes, framework info, and lot count
- **Procurement history** — for FaT tenders, expands the full notice family (UK1 → UK2 → UK3 → UK4 → UK6) in chronological order
- **AI analysis** — Go / No-go assessment via local Ollama (requires Ollama running with a supported model)
- Export filtered results to CSV
- Manual refresh trigger

---

## Production Deployment

The API is stateless; the in-memory cache resets on restart.

For production:

1. **Persist cache** — replace `InMemoryCache` in `app/dependencies.py` with Redis
2. **Run with Gunicorn** — `gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app`
3. **Set CORS** — update `CORS_ORIGINS` to your deployed dashboard domain
4. **FaT API key** — register at find-tender.service.gov.uk to get a key and set `FAT_API_KEY` for higher rate limits
5. **Build the dashboard** — `cd dashboard && npm run build`, then serve `dist/` via a static host or the FastAPI `StaticFiles` mount
