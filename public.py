# public.py
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import json, time, io, csv, ipaddress

from db import get_conn

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
    """
    Accepts:
    {
      "path": "/shen",
      "visitor_info": { ... full geo/meta json ... },
      "ref": "https://ref.example"
    }
    Returns: { id, path, visitor_info }
    """
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

        conn = get_conn()
        cur = conn.cursor()
        # Upsert by (page, ip): repeat same page+ip updates latest info; new ip creates new row
        cur.execute(
            """
            INSERT INTO public_events (page, ref, ip, user_agent, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(page, ip) DO UPDATE SET
                ref = excluded.ref,
                user_agent = excluded.user_agent,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (body.path, body.ref, ip, ua, json.dumps(payload), int(time.time()))
        )
        conn.commit()

        cur.execute(
            "SELECT id FROM public_events WHERE page = ? AND ip = ? ORDER BY id DESC LIMIT 1",
            (body.path, ip)
        )
        row = cur.fetchone()
        conn.close()

        return JSONResponse(content={
            "id": row["id"] if row else None,
            "path": body.path,
            "visitor_info": body.visitor_info or {}
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/public")
def list_public(
    page: Optional[str] = Query(default=None, description="Filter by path substring"),
    q: Optional[str] = Query(default=None, description="Substring search in payload/user_agent/ref"),
    include_payload: bool = Query(default=False, description="Include visitor_info in response"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
):
    conn = get_conn()
    cur = conn.cursor()

    where, params = [], []
    if page:
        where.append("page LIKE ?"); params.append(f"%{page}%")
    if q:
        where.append("(payload LIKE ? OR user_agent LIKE ? OR ref LIKE ?)")
        like = f"%{q}%"; params.extend([like, like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    cur.execute(f"SELECT COUNT(*) AS c FROM public_events {where_sql}", params)
    total = cur.fetchone()["c"]

    cur.execute(f"""
        SELECT id, page, ref, ip, user_agent, payload, created_at
        FROM public_events
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset])
    rows = cur.fetchall()
    conn.close()

    if include_payload:
        items = [{
            "id": r["id"],
            "path": r["page"],
            "visitor_info": (json.loads(r["payload"]).get("data", {}).get("meta", {}) if r["payload"] else {}),
            "created_at": r["created_at"],
        } for r in rows]
    else:
        items = [{
            "id": r["id"],
            "path": r["page"],
            "created_at": r["created_at"],
        } for r in rows]

    return {"total": total, "count": len(items), "offset": offset, "limit": limit, "items": items}

@router.get("/public/export")
def export_public(
    page: Optional[str] = Query(default=None, description="Filter by path substring"),
    q: Optional[str] = Query(default=None, description="Substring search in payload/user_agent/ref"),
    fmt: str = Query(default="json", pattern="^(json|csv)$", description="Export format: json or csv"),
    include_payload: bool = Query(default=False, description="Include visitor_info in JSON export"),
):
    conn = get_conn()
    cur = conn.cursor()

    where, params = [], []
    if page:
        where.append("page LIKE ?"); params.append(f"%{page}%")
    if q:
        where.append("(payload LIKE ? OR user_agent LIKE ? OR ref LIKE ?)")
        like = f"%{q}%"; params.extend([like, like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    cur.execute(f"""
        SELECT id, page, ref, ip, user_agent, payload, created_at
        FROM public_events
        {where_sql}
        ORDER BY id DESC
    """, params)
    rows = cur.fetchall()
    conn.close()

    if fmt == "json":
        if include_payload:
            items = [{
                "id": r["id"],
                "path": r["page"],
                "visitor_info": (json.loads(r["payload"]).get("data", {}).get("meta", {}) if r["payload"] else {}),
                "created_at": r["created_at"],
            } for r in rows]
        else:
            items = [{
                "id": r["id"],
                "path": r["page"],
                "created_at": r["created_at"],
            } for r in rows]
        return JSONResponse(content=items)

    # CSV export: flat concise columns
    def to_csv():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "path", "ip", "created_at"])
        for r in rows:
            writer.writerow([r["id"], r["page"], r["ip"], r["created_at"]])
        yield buffer.getvalue()

    return StreamingResponse(
        to_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=public_events_export.csv"},
    )
