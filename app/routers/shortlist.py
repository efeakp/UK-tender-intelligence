"""
Shortlist & bid feedback — persistent JSON file storage.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/shortlist", tags=["Shortlist"])

SHORTLIST_FILE = Path("shortlist_data.json")


def _load() -> dict:
    if SHORTLIST_FILE.exists():
        try:
            return json.loads(SHORTLIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict):
    SHORTLIST_FILE.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ShortlistAdd(BaseModel):
    tender_title: str
    tender_authority: Optional[str] = None
    tender_value: Optional[str] = None
    tender_deadline: Optional[str] = None
    tender_score: Optional[float] = None
    tender_source: Optional[str] = None
    tender_url: Optional[str] = None
    shortlisted_by: Optional[str] = None


class FeedbackUpdate(BaseModel):
    bid_decision: Optional[str] = None   # go | no-go | under-review | bid-submitted
    confidence: Optional[str] = None     # high | medium | low
    score_accuracy: Optional[int] = None # 1–5 (1=AI was wrong, 5=spot-on)
    team_notes: Optional[str] = None
    management_notes: Optional[str] = None
    outcome: Optional[str] = None        # pending | won | lost | withdrawn
    updated_by: Optional[str] = None


@router.get("")
async def list_shortlist():
    return list(_load().values())


@router.post("/{tender_id}")
async def add_to_shortlist(tender_id: str, entry: ShortlistAdd):
    data = _load()
    if tender_id not in data:
        data[tender_id] = {
            "tender_id": tender_id,
            **entry.model_dump(),
            "shortlisted_at": _now(),
            "feedback": {
                "bid_decision": "under-review",
                "confidence": None,
                "score_accuracy": None,
                "team_notes": "",
                "management_notes": "",
                "outcome": "pending",
                "updated_at": None,
                "updated_by": None,
            },
        }
        _save(data)
    return data[tender_id]


@router.delete("/{tender_id}")
async def remove_from_shortlist(tender_id: str):
    data = _load()
    if tender_id not in data:
        raise HTTPException(status_code=404, detail="Not in shortlist")
    del data[tender_id]
    _save(data)
    return {"status": "removed", "tender_id": tender_id}


@router.put("/{tender_id}/feedback")
async def update_feedback(tender_id: str, body: FeedbackUpdate):
    data = _load()
    if tender_id not in data:
        raise HTTPException(status_code=404, detail="Not in shortlist")
    fb = data[tender_id].setdefault("feedback", {})
    for k, v in body.model_dump(exclude_none=True).items():
        fb[k] = v
    fb["updated_at"] = _now()
    _save(data)
    return data[tender_id]


@router.get("/report")
async def management_report():
    entries = list(_load().values())

    def _count(key_fn):
        counts: dict = {}
        for e in entries:
            k = key_fn(e)
            counts[k] = counts.get(k, 0) + 1
        return counts

    return {
        "total": len(entries),
        "by_decision": _count(lambda e: e.get("feedback", {}).get("bid_decision") or "unknown"),
        "by_outcome":  _count(lambda e: e.get("feedback", {}).get("outcome") or "pending"),
        "avg_score_accuracy": (
            sum(e["feedback"]["score_accuracy"] for e in entries if (e.get("feedback") or {}).get("score_accuracy"))
            / max(1, sum(1 for e in entries if (e.get("feedback") or {}).get("score_accuracy")))
        ),
        "entries": entries,
    }


@router.get("/export/csv")
async def export_csv():
    entries = list(_load().values())
    rows = [
        "Tender ID,Title,Authority,Value,Deadline,AI Score,Source,"
        "Bid Decision,Confidence,Score Accuracy,Outcome,Team Notes,Management Notes,Shortlisted At"
    ]
    for e in entries:
        fb = e.get("feedback", {})

        def q(v):
            return '"' + str(v or "").replace('"', '""') + '"'

        rows.append(",".join([
            q(e.get("tender_id")),
            q(e.get("tender_title")),
            q(e.get("tender_authority")),
            q(e.get("tender_value")),
            q(e.get("tender_deadline")),
            q(e.get("tender_score")),
            q(e.get("tender_source")),
            q(fb.get("bid_decision")),
            q(fb.get("confidence")),
            q(fb.get("score_accuracy")),
            q(fb.get("outcome")),
            q(fb.get("team_notes")),
            q(fb.get("management_notes")),
            q(e.get("shortlisted_at")),
        ]))
    csv_text = "\n".join(rows)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="nordic-shortlist.csv"'},
    )
