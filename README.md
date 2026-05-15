# Nordic Energy — Tender Intelligence API

FastAPI backend that proxies **Find a Tender (FaT)** and **Contracts Finder (CF)**,
scores results against Nordic Energy's business scope, caches data, and serves
the React dashboard.

## Project Structure

```
nordic-tender-api/
├── app/
│   ├── main.py               # FastAPI app, CORS, startup
│   ├── config.py             # Settings via .env
│   ├── dependencies.py       # Shared deps (cache, http client)
│   ├── models/
│   │   ├── tender.py         # Pydantic schemas
│   │   └── filters.py        # Query param models
│   ├── routers/
│   │   ├── tenders.py        # GET /tenders, GET /tenders/{id}
│   │   ├── sources.py        # GET /sources (health + stats)
│   │   └── refresh.py        # POST /refresh (manual trigger)
│   └── services/
│       ├── scorer.py         # Relevance scoring engine
│       ├── find_a_tender.py  # Find a Tender API client
│       ├── contracts_finder.py  # Contracts Finder API client
│       ├── aggregator.py     # Merge + deduplicate sources
│       └── scheduler.py      # APScheduler daily refresh job
├── tests/
│   ├── test_scorer.py
│   ├── test_aggregator.py
│   └── test_api.py
├── .env.example
├── requirements.txt
└── README.md
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env as needed (defaults work out of the box)

# 3. Run dev server
uvicorn app.main:app --reload --port 8000

# 4. View API docs
open http://localhost:8000/docs
```

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tenders` | List tenders with filters + scoring |
| GET | `/tenders/{id}` | Single tender detail |
| GET | `/sources` | Source health + last-fetch stats |
| POST | `/refresh` | Manually trigger a data refresh |
| GET | `/health` | API health check |

## Environment Variables

See `.env.example` for all options. Key ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_TTL_MINUTES` | `60` | How long to cache API results |
| `REFRESH_CRON` | `0 7 * * *` | Daily refresh schedule (7am UTC) |
| `MIN_SCORE_DEFAULT` | `3` | Default minimum relevance score |
| `FAT_PAGE_SIZE` | `50` | Results per page from Find a Tender |
| `CF_PAGE_SIZE` | `50` | Results per page from Contracts Finder |
| `CORS_ORIGINS` | `http://localhost:3000` | Allowed frontend origins |

## Relevance Scoring

Each tender is scored 0–10 against Nordic Energy's keyword taxonomy:

- **Energy generation / renewables** — solar, wind, biomass, tidal, CHP, etc.
- **Heat networks / district energy** — district heating, heat pump, thermal storage, etc.
- **Energy consulting / advisory** — ESCO, EPC, net zero, decarbonisation, etc.

Multi-word keyword matches score 2 points; single-word matches score 1.
Score is capped at 10.

## Production Deployment

The API is stateless — cache lives in-process by default. For production:

1. Replace `InMemoryCache` with **Redis** (`app/dependencies.py`)
2. Deploy on **Azure App Service** or behind an **Azure API Management** gateway
3. Set `CORS_ORIGINS` to your dashboard domain
4. Use `gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app`
