"""
POST /tenders/{id}/summarise — AI-powered tender analysis via Claude API.

Returns for each tender:
  - 2-sentence plain-English summary
  - Go / No-go / Consider recommendation with reasoning
  - Key requirements (deadline, budget, certifications, experience)
  - Fit assessment against Nordic Energy's four services
"""

import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app.dependencies import cache, CACHE_KEY_TENDERS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tenders", tags=["AI"])

# Ollama runs locally — no API key required
OLLAMA_API_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL   = "gemma3:4b"

NORDIC_ENERGY_CONTEXT = """
Nordic Energy is a UK energy consultancy delivering four core services:

Service 01 — Renewable Energy Opportunity Identification
  3D spatial modelling, GIS analysis, LAEP production, energy masterplans,
  site identification for solar, wind, heat networks and hybrid solutions
  at city and regional scale. Uses in-house digital tools such as Odin and Loki.

Service 02 — Energy Feasibility Studies
  RIBA Stage 2 feasibility studies, techno-economic assessments, option
  appraisals, desktop feasibility reports for renewable energy and heat
  network projects. Experienced with DESNZ-funded programmes.

Service 03 — Energy System Optimisation
  Heat network performance reviews, grid connection studies, private wire,
  battery storage, demand flexibility, heat pump integration, district
  heating optimisation.

Service 04 — Business Case Development
  OBC/FBC development, financial modelling, grant funding applications
  (HNDU, GHFF, LIFF, UKSPF), investment readiness, commercial models
  for public-private partnerships and community energy projects.

Typical clients: Combined Authorities, Local Authorities, National Park
Authorities, DESNZ, housing associations, energy developers.

Recent wins include: Energy Generation Accelerator Programme for York and
North Yorkshire Combined Authority (spatial analysis + 20 feasibility
studies), PANZ.

Nordic Energy is registered on: DESNZ HNDU framework, ENZPS framework,
CCS Demand Management and Renewables DPS (RM6313), CPCA DPS, Yortender,
Bloom, ProContract, BlueLight services.
"""


class TenderSummary(BaseModel):
    tender_id:       str
    summary:         str
    recommendation:  str           # "Go" | "No-go" | "Consider"
    reasoning:       str
    key_requirements: List[str]
    fit_assessment:  str
    matched_services: List[str]
    confidence:      str           # "High" | "Medium" | "Low"


def _get_tender(tender_id: str):
    tenders = cache.get(CACHE_KEY_TENDERS)
    if not tenders:
        raise HTTPException(status_code=503, detail="Cache not populated")
    for t in tenders:
        if t.id == tender_id:
            return t
    raise HTTPException(status_code=404, detail=f"Tender '{tender_id}' not found")


@router.post("/{tender_id}/summarise", response_model=TenderSummary, summary="AI tender analysis")
async def summarise_tender(tender_id: str):
    """
    Send a tender to LLM for AI-powered analysis.

    Returns a structured assessment including:
    - Plain-English summary (2 sentences)
    - Go/No-go/Consider recommendation with reasoning
    - Key requirements extracted from the description
    - Fit assessment against Nordic Energy's capabilities
    """
    tender = _get_tender(tender_id)

    prompt = (
        "You are a bid manager at Nordic Energy, a UK energy consultancy. "
        "Analyse this public procurement tender and provide a structured assessment.\n\n"
        f"NORDIC ENERGY CONTEXT:\n{NORDIC_ENERGY_CONTEXT}\n\n"
        "TENDER DETAILS:\n"
        f"Title: {tender.title}\n"
        f"Authority: {tender.authority}\n"
        f"Source: {tender.source}\n"
        f"Category: {tender.category}\n"
        f"Value: {tender.value}\n"
        f"Published: {tender.published.strftime('%d %B %Y') if tender.published else 'Unknown'}\n"
        f"Deadline: {tender.deadline.strftime('%d %B %Y') if tender.deadline else 'Unknown'}\n"
        f"CPV Codes: {', '.join(tender.cpv_codes) if tender.cpv_codes else 'Not specified'}\n"
        f"Relevance Score: {tender.score}/10\n\n"
        f"DESCRIPTION:\n{tender.description or 'No description available.'}\n\n"
        "Respond ONLY with a raw JSON object. No explanation, no markdown, no code blocks. "
        "Start your response with { and end with }:\n"
        "{\n"
        '  \"summary\": \"2-sentence plain-English summary of what this tender is asking for\",\n'
        '  \"recommendation\": \"Go or No-go or Consider\",\n'
        '  \"reasoning\": \"2-3 sentences explaining the recommendation\",\n'
        '  \"key_requirements\": [\"requirement 1\", \"requirement 2\", \"requirement 3\"],\n'
        '  \"fit_assessment\": \"2-3 sentences on how well Nordic Energy matches the requirements\",\n'
        '  \"matched_services\": [\"Service 01: Renewable Energy Opportunity Identification\"],\n'
        '  \"confidence\": \"High or Medium or Low\"\n'
        "}\n\n"
        "Rules: recommendation must be Go, No-go, or Consider. "
        "confidence must be High, Medium, or Low. "
        "key_requirements: 3-5 items. "
        "matched_services: only services that genuinely apply."
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                OLLAMA_API_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "model":  OLLAMA_MODEL,
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": 0.1},
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # Ollama response: data["message"]["content"]
        text = data.get("message", {}).get("content", "")
        logger.debug("Ollama raw response for %s: %s", tender_id, text[:500])

        # Parse JSON robustly — gemma3 sometimes wraps output or adds preamble
        import json, re

        # Strip markdown fences
        text = re.sub(r"```json|```", "", text).strip()

        # Extract the first complete JSON object found anywhere in the response.
        # This handles cases where the model adds text before or after the JSON.
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            logger.error("No JSON object in Ollama response for %s: %s", tender_id, text[:300])
            raise ValueError(f"Model did not return valid JSON. Response: {text[:150]}")

        result = json.loads(json_match.group())

        return TenderSummary(
            tender_id        = tender_id,
            summary          = result.get("summary", ""),
            recommendation   = result.get("recommendation", "Consider"),
            reasoning        = result.get("reasoning", ""),
            key_requirements = result.get("key_requirements", []),
            fit_assessment   = result.get("fit_assessment", ""),
            matched_services = result.get("matched_services", []),
            confidence       = result.get("confidence", "Medium"),
        )

    except httpx.HTTPStatusError as e:
        logger.error("Ollama API error: %s", e)
        raise HTTPException(status_code=502, detail=f"Ollama error: {e.response.status_code}. Is Ollama running? Run: ollama serve")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to Ollama. Make sure it is running: ollama serve")
    except Exception as e:
        logger.error("Summarisation failed for %s: %s", tender_id, e)
        raise HTTPException(status_code=500, detail=f"Summarisation failed: {e}")