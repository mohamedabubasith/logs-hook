# event.py
from fastapi import APIRouter, Request, HTTPException, Query, Path
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import json, time, io, csv

from db import get_conn

router = APIRouter(prefix="", tags=["events"])
MAX_LIMIT = 200

class BaseEvent(BaseModel):
    type: str = Field(..., description="event type: log|analytics|login")
    user_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

def client_ip(req: Request) -> Optional[str]:
    xff = req.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",").strip()
    return req.client.host if req.client else None

@router.post("/hook")
async def hook(req: Request, evt: BaseEvent):
    try:
        ip = client_ip(req)
        ua = req.headers.get("user-agent")
        payload = evt.model_dump()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO webhook_events (event_type, user_id, ip, user_agent, payload, created_at) VALUES (?,?,?,?,?,?)",
            (evt.type, evt.user_id, ip, ua, json.dumps(payload), int(time.time()))
        )
        conn.commit()
        conn.close()
        return {"ok": True, "stored": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/events")
def list_events(
    event_type: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    from_ts: Optional[int] = Query(default=None),
    to_ts: Optional[int] = Query(default=None),
    q: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
):
    conn = get_conn()
    cur = conn.cursor()

    where, params = [], []
    if event_type:
        where.append("event_type = ?"); params.append(event_type)
    if user_id:
        where.append("user_id = ?"); params.append(user_id)
    if from_ts is not None:
        where.append("created_at >= ?"); params.append(from_ts)
    if to_ts is not None:
        where.append("created_at <= ?"); params.append(to_ts)
    if q:
        where.append("(payload LIKE ? OR user_agent LIKE ?)")
        like = f"%{q}%"; params.extend([like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    cur.execute(f"SELECT COUNT(*) AS c FROM webhook_events {where_sql}", params)
    total = cur.fetchone()["c"]

    cur.execute(f"""
        SELECT id, event_type, user_id, ip, user_agent, payload, created_at
        FROM webhook_events
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset])
    rows = cur.fetchall()
    conn.close()

    items = [{
        "id": r["id"],
        "type": r["event_type"],
        "user_id": r["user_id"],
        "ip": r["ip"],
        "user_agent": r["user_agent"],
        "payload": json.loads(r["payload"]) if r["payload"] else None,
        "created_at": r["created_at"],
    } for r in rows]

    return {"total": total, "count": len(items), "offset": offset, "limit": limit, "items": items}

@router.get("/events/export")
def export_events(
    event_type: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    from_ts: Optional[int] = Query(default=None),
    to_ts: Optional[int] = Query(default=None),
    q: Optional[str] = Query(default=None),
    fmt: str = Query(default="json", pattern="^(json|csv)$"),
):
    conn = get_conn()
    cur = conn.cursor()

    where, params = [], []
    if event_type:
        where.append("event_type = ?"); params.append(event_type)
    if user_id:
        where.append("user_id = ?"); params.append(user_id)
    if from_ts is not None:
        where.append("created_at >= ?"); params.append(from_ts)
    if to_ts is not None:
        where.append("created_at <= ?"); params.append(to_ts)
    if q:
        where.append("(payload LIKE ? OR user_agent LIKE ?)")
        like = f"%{q}%"; params.extend([like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cur.execute(f"""
        SELECT id, event_type, user_id, ip, user_agent, payload, created_at
        FROM webhook_events
        {where_sql}
        ORDER BY id DESC
    """, params)
    rows = cur.fetchall()
    conn.close()

    if fmt == "json":
        items = [{
            "id": r["id"],
            "type": r["event_type"],
            "user_id": r["user_id"],
            "ip": r["ip"],
            "user_agent": r["user_agent"],
            "payload": json.loads(r["payload"]) if r["payload"] else None,
            "created_at": r["created_at"],
        } for r in rows]
        return JSONResponse(content=items)

    def to_csv():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "type", "user_id", "ip", "user_agent", "payload", "created_at"])
        for r in rows:
            writer.writerow([r["id"], r["event_type"], r["user_id"], r["ip"], r["user_agent"], r["payload"], r["created_at"]])
        yield buffer.getvalue()

    return StreamingResponse(
        to_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=events_export.csv"},
    )

@router.delete("/events/{event_id}")
def delete_event_by_id(event_id: int = Path(..., ge=1)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM webhook_events WHERE id = ?", (event_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        # 404 if nothing was deleted
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True, "deleted": deleted}

@router.delete("/events")
def delete_events(
    event_type: Optional[str] = Query(default=None, description="Filter by type"),
    user_id: Optional[str] = Query(default=None, description="Filter by user id"),
    from_ts: Optional[int] = Query(default=None, description="Unix epoch secs from (inclusive)"),
    to_ts: Optional[int] = Query(default=None, description="Unix epoch secs to (inclusive)"),
    q: Optional[str] = Query(default=None, description="Substring search in payload/user_agent"),
    confirm: bool = Query(default=False, description="Must be true to execute delete"),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to execute delete")
    conn = get_conn()
    cur = conn.cursor()

    where, params = [], []
    if event_type:
        where.append("event_type = ?"); params.append(event_type)
    if user_id:
        where.append("user_id = ?"); params.append(user_id)
    if from_ts is not None:
        where.append("created_at >= ?"); params.append(from_ts)
    if to_ts is not None:
        where.append("created_at <= ?"); params.append(to_ts)
    if q:
        where.append("(payload LIKE ? OR user_agent LIKE ?)")
        like = f"%{q}%"; params.extend([like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cur.execute(f"DELETE FROM webhook_events {where_sql}", params)
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "deleted": deleted}