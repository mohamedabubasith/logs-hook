# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import init_db
from event import router as event_router
from public import router as public_router

app = FastAPI()

# CORS: loosen for local dev; restrict in prod
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # e.g., ["https://your-portfolio.com"]
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

# Mount routers
app.include_router(event_router)
app.include_router(public_router)
