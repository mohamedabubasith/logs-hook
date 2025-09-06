# app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from time import time

from db import init_db, close_db
from event import router as event_router
from public import router as public_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def health():
        return {"ok": True, "status": "healthy", "ts": int(time())}

    app.include_router(event_router)
    app.include_router(public_router)
    return app

app = create_app()
