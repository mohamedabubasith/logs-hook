# public.py
from fastapi import APIRouter, Request, HTTPException, Query, Path
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import json, time, io, csv, ipaddress

from db import PublicEvent

router = APIRouter(prefix="", tags=["public"])
MAX_LIMIT = 200

class PublicIn(BaseModel):
    path: str = Field(..., description="Visited path, e.g., /shen")
    visitor_info: Dict[str, Any] = Field(default_factory=dict, description="Full visitor meta JSON from frontend")
    ref: Optional[str] = Field(default=None, description="Referrer URL if any")

def _normalize_header_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = ",".join(str(x) for x in v if x is not None)
    elif not isinstance(v, str):
        v = str(v)
    return v.strip()

def _first_ip_from_xff(xff_value: str) -> Optional[str]:
    if not xff_value:
        return None
    for part in xff_value.split(","):
        ip = part.strip().strip("'\"")
        if ip:
            return ip
    return None

def _valid_ip(s: Optional[str]) -> bool:
    if not s:
        return False
    try:
        ipaddress.ip_address(s)
        return True
    except Exception:
        return False

def client_ip(req: Request) -> Optional[str]:
    xff_raw = _normalize_header_value(req.headers.get("x-forwarded-for"))
    ip = _first_ip_from_xff(xff_raw)
    if not _valid_ip(ip):
        xri = _normalize_header_value(req.headers.get("x-real-ip"))
        ip = xri if _valid_ip(xri) else None
    if not _valid_ip(ip):
        peer = getattr(req, "client", None)
        ip = peer.host if peer and _valid_ip(peer.host) else None
    return ip

def client_ua(req: Request) -> str:
    return _normalize_header_value(req.headers.get("user-agent"))

@router.post("/public")
async def public_track(req: Request, body: PublicIn):
    try:
        ip = client_ip(req)
        ua = client_ua(req)
        payload = {
            "path": body.path,
            "ref": body.ref,
            "data": {
                "meta": body.visitor_info or {},
                "client": {"ip": ip, "user_agent": ua}
            }
        }
        created_at = int(time.time())

        # Fix: Check for existing record by BOTH page AND ip (your original logic was correct)
        existing = await PublicEvent.find_one(PublicEvent.page == body.path, PublicEvent.ip == ip)
        
        if existing:
            # Update existing record for same path + same IP
            existing.ref = body.ref
            existing.user_agent = ua
            existing.payload = payload
            existing.created_at = created_at
            await existing.save()
            doc_id = str(existing.id)
            action = "updated"
        else:
            # Create new record for new IP or new path
            doc = PublicEvent(
                page=body.path, 
                ref=body.ref, 
                ip=ip, 
                user_agent=ua, 
                payload=payload, 
                created_at=created_at
            )
            saved = await doc.insert()
            doc_id = str(saved.id)
            action = "created"

        return JSONResponse(content={
            "id": doc_id, 
            "path": body.path, 
            "visitor_info": body.visitor_info or {},
            "action": action
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public")
async def list_public(
    page: Optional[str] = Query(default=None, description="Filter by path substring"),
    q: Optional[str] = Query(default=None, description="Substring search in payload/user_agent/ref"),
    include_payload: bool = Query(default=False, description="Include visitor_info in response"),
    offset: int = Query(default=0, ge=0),
    limit: Optional[str] = Query(default="50", description='Number of rows, or "all" to return everything'),
):
    try:
        filt: Dict[str, Any] = {}
        if page:
            filt["page"] = {"$regex": page, "$options": "i"}
        if q:
            filt["$or"] = [
                {"payload": {"$regex": q, "$options": "i"}},
                {"user_agent": {"$regex": q, "$options": "i"}},
                {"ref": {"$regex": q, "$options": "i"}},
            ]
        
        query = PublicEvent.find(filt)
        total = await query.count()

        # Fix: Sort by created_at descending (latest first) instead of -id
        if isinstance(limit, str) and limit.lower() == "all":
            docs = await query.sort(-PublicEvent.created_at).to_list()
            current_limit = total
            current_offset = 0
        else:
            try:
                lim_int = max(1, min(MAX_LIMIT, int(limit)))
            except Exception:
                lim_int = 50
            docs = await query.sort(-PublicEvent.created_at).skip(offset).limit(lim_int).to_list()
            current_limit = lim_int
            current_offset = offset

        if include_payload:
            items = [{
                "id": str(d.id),
                "path": d.page,
                "ip": d.ip,  # Include IP in response
                "user_agent": d.user_agent,  # Include user agent
                "ref": d.ref,  # Include referrer
                "visitor_info": ((d.payload or {}).get("data", {}) or {}).get("meta", {}) if isinstance(d.payload, dict) else {},
                "created_at": d.created_at,
            } for d in docs]
        else:
            items = [{
                "id": str(d.id),
                "path": d.page,
                "ip": d.ip,  # Include IP in basic response too
                "created_at": d.created_at,
            } for d in docs]

        return {
            "total": total, 
            "count": len(items), 
            "offset": current_offset, 
            "limit": current_limit, 
            "items": items
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/public/export")
async def export_public(
    page: Optional[str] = Query(default=None, description="Filter by path substring"),
    q: Optional[str] = Query(default=None, description="Substring search in payload/user_agent/ref"),
    fmt: str = Query(default="json", pattern="^(json|csv)$", description="Export format: json or csv"),
    include_payload: bool = Query(default=False, description="Include visitor_info in JSON export"),
):
    try:
        filt: Dict[str, Any] = {}
        if page:
            filt["page"] = {"$regex": page, "$options": "i"}
        if q:
            filt["$or"] = [
                {"payload": {"$regex": q, "$options": "i"}},
                {"user_agent": {"$regex": q, "$options": "i"}},
                {"ref": {"$regex": q, "$options": "i"}},
            ]
        
        # Fix: Sort by created_at descending for export too
        docs = await PublicEvent.find(filt).sort(-PublicEvent.created_at).to_list()

        if fmt == "json":
            if include_payload:
                items = [{
                    "id": str(d.id),
                    "path": d.page,
                    "ip": d.ip,
                    "user_agent": d.user_agent,
                    "ref": d.ref,
                    "visitor_info": ((d.payload or {}).get("data", {}) or {}).get("meta", {}) if isinstance(d.payload, dict) else {},
                    "created_at": d.created_at,
                } for d in docs]
            else:
                items = [{
                    "id": str(d.id),
                    "path": d.page,
                    "ip": d.ip,
                    "created_at": d.created_at,
                } for d in docs]
            return JSONResponse(content=items)

        def generate_csv():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            if include_payload:
                writer.writerow(["id", "path", "ip", "user_agent", "ref", "created_at", "visitor_info"])
                for d in docs:
                    vi = ((d.payload or {}).get("data", {}) or {}).get("meta", {}) if isinstance(d.payload, dict) else {}
                    writer.writerow([
                        str(d.id), 
                        d.page, 
                        d.ip or "", 
                        d.user_agent or "", 
                        d.ref or "", 
                        d.created_at, 
                        json.dumps(vi, ensure_ascii=False)
                    ])
            else:
                writer.writerow(["id", "path", "ip", "created_at"])
                for d in docs:
                    writer.writerow([str(d.id), d.page, d.ip or "", d.created_at])
            yield buffer.getvalue()

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=public_events_export.csv"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/public/{public_id}")
async def delete_public_by_id(public_id: str = Path(..., description="MongoDB id as string")):
    try:
        d = await PublicEvent.get(public_id)
        if not d:
            raise HTTPException(status_code=404, detail="Public event not found")
        await d.delete()
        return {"ok": True, "deleted": 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/public")
async def delete_public_events(
    page: Optional[str] = Query(default=None, description="Filter by page"),
    ip: Optional[str] = Query(default=None, description="Filter by IP"),
    from_ts: Optional[int] = Query(default=None, description="Unix epoch secs from (inclusive)"),
    to_ts: Optional[int] = Query(default=None, description="Unix epoch secs to (inclusive)"),
    confirm: bool = Query(default=False, description="Must be true to execute delete"),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to execute delete")
    try:
        conditions = []
        if page:
            conditions.append(PublicEvent.page == page)
        if ip:
            conditions.append(PublicEvent.ip == ip)
        if from_ts is not None or to_ts is not None:
            rng = {}
            if from_ts is not None:
                rng["$gte"] = from_ts
            if to_ts is not None:
                rng["$lte"] = to_ts
            conditions.append({"created_at": rng})
            
        if conditions:
            result = await PublicEvent.find({"$and": conditions}).delete()
        else:
            result = await PublicEvent.delete_all()
            
        deleted_count = result.deleted_count if hasattr(result, 'deleted_count') else 0
        return {"ok": True, "deleted": deleted_count}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
