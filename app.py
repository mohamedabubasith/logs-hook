# app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import init_db
from event import router as event_router
from public import router as public_router
from time import time

def create_app() -> FastAPI:
    app = FastAPI()

    # CORS: relax for dev; restrict origins in prod
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],   # e.g., ["https://your-portfolio.com"]
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup():
        init_db()

    # Simple health check
    @app.get("/")
    def health():
        return {"ok": True, "status": "healthy", "ts": int(time())}

    # Routers
    app.include_router(event_router)
    app.include_router(public_router)

    return app

app = create_app()
