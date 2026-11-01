import os

from fastapi import FastAPI

from app.database import engine
from app.models.auth_models import Base
from app.routers import auth

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ArcReel Bridge (auth)")
app.include_router(auth.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"ready": True}
