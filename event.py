# event.py
from fastapi import APIRouter, Request, HTTPException, Query, Path
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import json, time, io, csv

from db import WebhookEvent

router = APIRouter(prefix="", tags=["events"])
MAX_LIMIT = 200

class BaseEvent(BaseModel):
    type: str = Field(..., description="event type: log|analytics|login")
    user_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

def client_ip(req: Request) -> Optional[str]:
    xff = req.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return req.client.host if req.client else None

@router.post("/hook")
async def hook(req: Request, evt: BaseEvent):
    try:
        ip = client_ip(req)
        ua = req.headers.get("user-agent")
        payload = evt.model_dump()
        doc = WebhookEvent(
            event_type=evt.type,
            user_id=evt.user_id,
            ip=ip,
            user_agent=ua,
            payload=payload,
            created_at=int(time.time()),
        )
        saved = await doc.insert()
        return {"ok": True, "stored": True, "id": str(saved.id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/events")
async def list_events(
    event_type: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    from_ts: Optional[int] = Query(default=None),
    to_ts: Optional[int] = Query(default=None),
    q: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
):
    try:
        conditions = []
        if event_type:
            conditions.append(WebhookEvent.event_type == event_type)
        if user_id:
            conditions.append(WebhookEvent.user_id == user_id)
        
        # Fix: Proper time range query syntax
        if from_ts is not None or to_ts is not None:
            rng = {}
            if from_ts is not None:
                rng["$gte"] = from_ts
            if to_ts is not None:
                rng["$lte"] = to_ts
            conditions.append({"created_at": rng})
            
        if q:
            conditions.append({
                "$or": [
                    {"payload": {"$regex": q, "$options": "i"}},
                    {"user_agent": {"$regex": q, "$options": "i"}},
                ]
            })

        if conditions:
            query = WebhookEvent.find({"$and": conditions})
        else:
            query = WebhookEvent.find()
            
        total = await query.count()
        docs = await query.sort(-WebhookEvent.created_at).skip(offset).limit(limit).to_list()

        items = [{
            "id": str(d.id),
            "type": d.event_type,
            "user_id": d.user_id,
            "ip": d.ip,
            "user_agent": d.user_agent,
            "payload": d.payload,
            "created_at": d.created_at,
        } for d in docs]

        return {"total": total, "count": len(items), "offset": offset, "limit": limit, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/events/export")
async def export_events(
    event_type: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    from_ts: Optional[int] = Query(default=None),
    to_ts: Optional[int] = Query(default=None),
    q: Optional[str] = Query(default=None),
    fmt: str = Query(default="json", pattern="^(json|csv)$"),
):
    try:
        conditions = []
        if event_type:
            conditions.append(WebhookEvent.event_type == event_type)
        if user_id:
            conditions.append(WebhookEvent.user_id == user_id)
            
        # Fix: Consistent time range query syntax
        if from_ts is not None or to_ts is not None:
            rng = {}
            if from_ts is not None:
                rng["$gte"] = from_ts
            if to_ts is not None:
                rng["$lte"] = to_ts
            conditions.append({"created_at": rng})
            
        if q:
            conditions.append({"$or": [
                {"payload": {"$regex": q, "$options": "i"}},
                {"user_agent": {"$regex": q, "$options": "i"}},
            ]})

        if conditions:
            docs = await WebhookEvent.find({"$and": conditions}).sort(-WebhookEvent.created_at).to_list()
        else:
            docs = await WebhookEvent.find().sort(-WebhookEvent.created_at).to_list()

        items = [{
            "id": str(d.id),
            "type": d.event_type,
            "user_id": d.user_id,
            "ip": d.ip,
            "user_agent": d.user_agent,
            "payload": d.payload,
            "created_at": d.created_at,
        } for d in docs]

        if fmt == "json":
            return JSONResponse(content=items)

        def generate_csv():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["id", "type", "user_id", "ip", "user_agent", "payload", "created_at"])
            for r in items:
                writer.writerow([
                    r.get("id"),
                    r.get("type"),
                    r.get("user_id") or "",
                    r.get("ip") or "",
                    r.get("user_agent") or "",
                    json.dumps(r.get("payload", {}), ensure_ascii=False),
                    r.get("created_at") or "",
                ])
            yield buffer.getvalue()

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=events_export.csv"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/events/{event_id}")
async def delete_event_by_id(event_id: str = Path(..., description="MongoDB id as string")):
    try:
        d = await WebhookEvent.get(event_id)
        if not d:
            raise HTTPException(status_code=404, detail="Event not found")
        await d.delete()
        return {"ok": True, "deleted": 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/events")
async def delete_events(
    event_type: Optional[str] = Query(default=None, description="Filter by type"),
    user_id: Optional[str] = Query(default=None, description="Filter by user id"),
    from_ts: Optional[int] = Query(default=None, description="Unix epoch secs from (inclusive)"),
    to_ts: Optional[int] = Query(default=None, description="Unix epoch secs to (inclusive)"),
    q: Optional[str] = Query(default=None, description="Substring search in payload/user_agent"),
    confirm: bool = Query(default=False, description="Must be true to execute delete"),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to execute delete")
    try:
        conditions = []
        if event_type:
            conditions.append(WebhookEvent.event_type == event_type)
        if user_id:
            conditions.append(WebhookEvent.user_id == user_id)
            
        # Fix: Consistent time range query syntax
        if from_ts is not None or to_ts is not None:
            rng = {}
            if from_ts is not None:
                rng["$gte"] = from_ts
            if to_ts is not None:
                rng["$lte"] = to_ts
            conditions.append({"created_at": rng})
            
        if q:
            conditions.append({"$or": [
                {"payload": {"$regex": q, "$options": "i"}},
                {"user_agent": {"$regex": q, "$options": "i"}},
            ]})
            
        if conditions:
            result = await WebhookEvent.find({"$and": conditions}).delete()
        else:
            result = await WebhookEvent.delete_all()
            
        deleted_count = result.deleted_count if hasattr(result, 'deleted_count') else 0
        return {"ok": True, "deleted": deleted_count}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
