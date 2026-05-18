# UK Tender Intelligence Platform — Management Presentation

> **Usage:** Each `---` separator is a slide break. Copy into Google Slides
> (File → Import slides → Upload), paste into PowerPoint, or hand to a designer.
> Speaker notes are indented below each slide.

---

## Slide 1 — Title

# UK Tender Intelligence Platform
### Automated procurement monitoring for Nordic Energy

**May 2026**

> *Speaker notes:*
> This presentation covers the internal tool we have built to monitor UK public
> procurement and surface opportunities relevant to Nordic Energy's four core
> service lines. It replaced a manual, ad-hoc process that was missing bids.

---

## Slide 2 — The Problem

# The Problem

**UK public bodies publish thousands of procurement notices every month.**

- Energy consultancy tenders are scattered across four separate government portals
- Each portal has its own format, search UI, and date logic
- Manual monitoring takes several hours per week — and still misses notices
- By the time a relevant notice is found, the submission window may already be closing
- No visibility of the pipeline: planning notices that precede a live tender by weeks

> *Speaker notes:*
> The four portals are Find a Tender, Contracts Finder, Sell2Wales, and Public
> Contracts Scotland. None of them have a cross-portal search. Before this tool,
> someone had to visit each one, run keyword searches, and manually filter results.
> Pipeline notices (Prior Information Notices) were almost never caught in time to
> influence scope or brief the bid team early.

---

## Slide 3 — The Solution

# The Solution

A **fully automated** internal platform that:

| What it does | How |
|---|---|
| Monitors all four UK procurement portals | Runs daily at 7 AM |
| Scores every notice against Nordic Energy's services | Keyword + CPV taxonomy |
| Removes duplicates across portals | Fuzzy title matching |
| Surfaces the highest-priority opportunities first | Score 0–10 ranking |
| Sends a morning digest to Teams | Scheduled webhook |

**No manual searching. No missed deadlines.**

> *Speaker notes:*
> The platform runs as a background service. Every morning it fetches the latest
> notices, scores them, and sends a ranked summary to the team's Teams channel.
> Staff can also open the live dashboard at any time to filter, search, and export.

---

## Slide 4 — Data Sources

# Four Government Sources, One View

| Source | Coverage | Volume (typical) |
|---|---|---|
| **Find a Tender (FaT)** | England, Wales, Northern Ireland | ~300–500 notices / 30 days |
| **Contracts Finder (CF)** | England | ~400–600 notices / 30 days |
| **Sell2Wales** | Wales | ~50–100 notices / 30 days |
| **Public Contracts Scotland** | Scotland | ~80–150 notices / 30 days |

**All above and below the procurement threshold. All procurement stages.**

> *Speaker notes:*
> Find a Tender covers above-threshold contracts under the Procurement Act 2023.
> Contracts Finder covers smaller contracts (no minimum value). Sell2Wales and
> Public Contracts Scotland cover their respective devolved nations. Together
> they give full UK coverage. The platform deduplicates notices that appear on
> more than one portal.

---

## Slide 5 — Procurement Stage Coverage

# We See the Full Pipeline

```
PIPELINE          ENGAGEMENT        LIVE TENDER       AWARDED
────────────────────────────────────────────────────────────────
Prior Info    →   Market           →   Contract    →  Award
Notice (PIN)      Engagement           Notice          Notice
"Future           "Early               "Opportunity"   "Awarded
 Opportunity"      Engagement"                          Contract"

   Weeks/months before tender drops     ← Know early, bid better
```

**Catching pipeline notices early means we can shape the brief — before
competitors even know the tender exists.**

> *Speaker notes:*
> UK3 (Planned Procurement Notices) are flagged with urgency because the
> live tender (UK4) can follow with as little as 10 days' notice. The platform
> boosts these in the ranking so the bid team sees them immediately.
> Early Engagement notices (market consultation) are often an invitation to
> influence the specification — attending these is free pre-bid intelligence.

---

## Slide 6 — Relevance Scoring

# How We Know It's Relevant

Each notice is scored **0–10** against Nordic Energy's four service areas:

| Service | Examples of what we match |
|---|---|
| **Service 01** — Opportunity Identification | heat network zoning, LAEP, GIS, spatial analysis, RESP |
| **Service 02** — Feasibility Studies | feasibility study, options appraisal, techno-economic, RIBA Stage 2 |
| **Service 03** — System Optimisation | heat pump, BESS, 5GDHC, PSDS, SHDF, district heating |
| **Service 04** — Business Case | business case, financial modelling, grant application, DESNZ |

**Hard filters** remove electricity supply contracts, waste, highways, catering, and payroll —
so the list is clean, not just large.

| Score | Label | Action |
|---|---|---|
| 7–10 | Strong match | Sent in daily Teams digest |
| 4–6 | Likely relevant | Visible in dashboard |
| 0–3 | Weak match | Hidden by default |

> *Speaker notes:*
> The scoring engine uses two layers: a keyword taxonomy of ~300 terms mapped
> to the four services, and a CPV code taxonomy of 56 codes. Multi-word phrases
> score higher than single words. Title matches score higher than description
> matches. Hard negative keywords (electricity supply contracts, grounds
> maintenance, etc.) zero-out the score immediately. The engine has been tuned
> against real won and lost bids.

---

## Slide 7 — The Dashboard

# Live Dashboard

**Access at any time — no login required on the internal network**

### What you can do:
- **Filter** by source, service area, procurement stage, UK region, and minimum score
- **Search** by keyword across title, authority, and description
- **Click any notice** to see full description, matched keywords, CPV codes, contract value, and lot count
- **Trace the lifecycle** — for Find a Tender notices, see the full history from pipeline through to award
- **Run AI analysis** — Go / No-go assessment via local AI model (Llama 3)
- **Export to CSV** — send the filtered list to the bid team or drop into a spreadsheet

> *Speaker notes:*
> The dashboard is a React web app served alongside the API. It talks to the
> same data the Teams digest uses. The procurement history view is particularly
> useful: you can see a UK1 pipeline notice from three months ago, the UK2
> market engagement notice, and the live UK4 tender all in one timeline.

---

## Slide 8 — Teams Digest

# Daily Morning Digest

**Sent automatically to the team's Teams channel every morning at 10:30 AM**

Each digest card includes:
- Notice title and contracting authority
- Contract value and deadline
- Score and matched service areas
- Direct link to the notice

**Only Score ≥ 7 (Strong) notices are sent** — typically 5–15 per day.

> *Speaker notes:*
> The digest uses the Microsoft Teams incoming webhook. It runs 15 minutes after
> the 7 AM data refresh so the data is always current. The threshold can be
> adjusted. High-likely (score 6) notices can be included by changing one
> environment variable.

---

## Slide 9 — How It Works (Technical Summary)

# Under the Hood

```
  07:00 UTC — Daily refresh starts
  ├── Fetch FaT (Find a Tender)          ──┐
  ├── Fetch Contracts Finder             ──┤  Concurrent
  ├── Fetch Sell2Wales                   ──┤  (all four in parallel)
  └── Fetch Public Contracts Scotland    ──┘
         ↓
  Deduplicate across sources (fuzzy title match, threshold 0.85)
         ↓
  Score each notice (keyword + CPV engine, 0–10)
         ↓
  Cache results (in-memory, 60-min live TTL + 48-hour fallback)
         ↓
  10:30 UTC — Teams digest sent
```

**FastAPI backend + React dashboard. Runs on a single VM. No database required.**

> *Speaker notes:*
> The backend is a Python FastAPI application. All four sources are fetched
> concurrently so the full refresh takes roughly 90–120 seconds rather than
> fetching them one by one. Each source has its own error handling and retry
> logic so a temporary outage at one portal doesn't affect the others.
> The 48-hour fallback cache means the dashboard always shows data even if the
> morning refresh has a transient failure.

---

## Slide 10 — Resilience

# What Happens When Things Go Wrong

| Scenario | Platform behaviour |
|---|---|
| Government portal is down | Falls back to yesterday's data for that source; other sources unaffected |
| Rate-limited (HTTP 429) | Exponential backoff with automatic retry |
| Transient DNS / network error | Short retry (10s × 2 attempts) before moving on |
| One source returns 0 results | Keeps last successful fetch; logs a warning |
| Morning refresh misses fire time | APScheduler catches up within 5 minutes |

**The dashboard has never served empty data since deployment.**

> *Speaker notes:*
> Resilience was a deliberate design priority after we saw the early version
> occasionally show blank source panels after a network blip. The per-source
> fallback cache and short-retry logic were specifically added to address this.

---

## Slide 11 — Business Impact

# Business Impact

### Time saved
> Manual monitoring across four portals: **~3–4 hours/week → ~15 minutes/week**

### Opportunities identified
> Pipeline (Future Opportunity + Early Engagement) notices now visible weeks
> before the live tender drops — previously missed entirely

### Bid quality
> Early sight of UK3 (Planned Procurement) notices gives the bid team time to
> attend market engagement events and shape the specification before writing starts

### Risk reduced
> Hard-negative filters eliminate irrelevant noise (electricity supply,
> catering, grounds maintenance) — no time wasted on out-of-scope notices

> *Speaker notes:*
> These are estimates based on the team's own assessment of the previous
> manual process. The platform has been live since April 2026. Actual bid win
> data will be tracked going forward.

---

## Slide 12 — Potential Next Steps

# Potential Next Steps

| Priority | Enhancement | Effort |
|---|---|---|
| High | Persist cache to Redis — survive server restarts | 1 day |
| High | Add email digest alternative for non-Teams users | 0.5 days |
| Medium | Competitor tracking — flag when known competitors win awards | 2 days |
| Medium | Deadline calendar view — Gantt-style bid pipeline | 2 days |
| Low | Automated bid/no-bid recommendation based on win history | 1 week |
| Low | Multi-user watchlists — each person monitors their own authority list | 3 days |

> *Speaker notes:*
> The platform is production-ready as it stands. The Redis migration is the
> only change needed before scaling to multiple workers. Everything else on
> this list is enhancement, not remediation.

---

## Slide 13 — Summary

# Summary

- **Problem:** UK procurement is fragmented across four portals. Manual monitoring misses bids.
- **Solution:** Automated daily aggregation, scoring, and alerting — one platform, four sources.
- **Coverage:** Every procurement stage, every UK nation, above and below threshold.
- **Intelligence:** 300+ keyword taxonomy tuned to Nordic Energy's four service lines.
- **Delivery:** Daily Teams digest at 10:30 AM + live dashboard available at any time.
- **Resilience:** Per-source fallback, automatic retry, 48-hour data persistence.

### No manual searching. No missed deadlines. More time to write better bids.

> *Speaker notes:*
> The platform is live and running. The team is using it daily. Any questions
> about functionality, data coverage, or next steps — happy to demo live.

---

*End of deck*
