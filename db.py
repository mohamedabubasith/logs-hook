# db.py
import os
import logging
import certifi
from typing import Optional, Dict, Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, IndexModel
from pymongo.server_api import ServerApi
from beanie import Document, init_beanie

log = logging.getLogger("db")

# Use your MongoDB URI directly here (set via env for safety in prod)
MONGODB_URI = os.environ.get(
    "MONGODB_URI",
    "mongodb+srv://abu:abu@abucluster.y8rtyqg.mongodb.net/?retryWrites=true&w=majority&appName=AbuCluster",
)
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "logsdb")

# ---------- Models ----------
class WebhookEvent(Document):
    event_type: str
    user_id: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    payload: Dict[str, Any]
    created_at: int

    class Settings:
        name = "webhook_events"
        indexes = [
            "event_type",
            "user_id",
            "created_at",
        ]

class PublicEvent(Document):
    page: str
    ref: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    payload: Dict[str, Any]
    created_at: int

    class Settings:
        name = "public_events"
        # Compound unique index on (page, ip)
        indexes = [
            IndexModel([("page", ASCENDING), ("ip", ASCENDING)], name="ux_public_events_page_ip", unique=True)
        ]

# ---------- Init / Close ----------
_client: Optional[AsyncIOMotorClient] = None

async def init_db():
    global _client
    if _client is not None:
        return
    log.info("Connecting to MongoDB (TLS enabled) for Beanie...")
    _client = AsyncIOMotorClient(
        MONGODB_URI,
        tls=True,
        tlsCAFile=certifi.where(),
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=30000,
        connectTimeoutMS=30000,
        socketTimeoutMS=30000,
    )
    # Verify connectivity
    await _client.admin.command("ping")
    await init_beanie(database=_client[MONGO_DB_NAME], document_models=[WebhookEvent, PublicEvent])
    log.info("MongoDB connected; Beanie initialized with models.")

async def close_db():
    global _client
    if _client:
        _client.close()
        _client = None
