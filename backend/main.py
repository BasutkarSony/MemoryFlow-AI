from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.agent_core import process_input
from backend.omi import sync_omi_to_qdrant, extract_transcript_text
from backend.qdrant_memory import remember, client, COLLECTION_NAME

app = FastAPI(title="Autonomous Memory Agent")
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "frontend")), name="static")


@app.get("/health")
def health():
    import os

    status = {
        "api": "ok",
        "qdrant": "disconnected",
        "lyzr": "not_configured",
        "omi": "not_configured",
    }

    try:
        client.get_collection(COLLECTION_NAME)
        status["qdrant"] = "connected"
    except Exception:
        status["qdrant"] = "disconnected"

    status["lyzr"] = (
        "configured" if os.getenv("LYZR_API_KEY") else "not_configured"
    )
    status["omi"] = (
        "configured" if os.getenv("OMI_API_KEY") else "not_configured"
    )

    return status



class ProcessRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return FileResponse("frontend/index.html")


@app.post("/process")
def process(request: ProcessRequest):
    return process_input(request.message)


@app.post("/omi/sync")
def omi_sync():
    return {"synced": sync_omi_to_qdrant()}


# Omi conversation webhook
@app.post("/omi/conversation")
def omi_conversation(payload: dict):
    """Receives a completed Omi conversation and stores it in Qdrant."""
    print("Omi conversation webhook received")
    text = extract_transcript_text(payload)
    if text:
        remember(text, memory_type="context", source="omi-webhook")
    return {
        "status": "received",
        "source": "omi",
        "type": "conversation",
        "stored": bool(text)
    }


# Omi realtime transcript webhook
@app.post("/omi/realtime")
def omi_realtime(payload: dict):
    """
    Receives realtime Omi transcript events.
    """
    print("Omi realtime webhook received")
    text = extract_transcript_text(payload)
    if text:
        remember(text, memory_type="context", source="omi-realtime")
    return {
        "status": "received",
        "source": "omi",
        "type": "realtime",
        "stored": bool(text)
    }