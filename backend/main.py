from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent_core import process_input
from backend.omi import OmiUnavailable, sync_omi_to_qdrant, store_omi_memory_records
from backend.qdrant_memory import MemoryStoreUnavailable, connection_status

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

    status["qdrant"] = connection_status()

    status["lyzr"] = (
        "configured" if os.getenv("LYZR_API_KEY") else "not_configured"
    )
    status["omi"] = (
        "configured" if os.getenv("OMI_API_KEY") else "not_configured"
    )

    return status



class ProcessRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)


@app.get("/")
def root():
    return FileResponse("frontend/index.html")


@app.post("/process")
def process(request: ProcessRequest):
    return process_input(request.message)


@app.post("/omi/sync")
def omi_sync():
    try:
        summary = sync_omi_to_qdrant()
        return {"synced": summary["stored"], **summary}
    except (OmiUnavailable, MemoryStoreUnavailable) as exc:
        return {"synced": 0, "error": str(exc), "received": 0, "stored": 0, "duplicates": 0, "skipped": 0}


# Omi conversation webhook
@app.post("/omi/conversation")
def omi_conversation(payload: dict):
    """Receives a completed Omi conversation and stores it in Qdrant."""
    try:
        writes = store_omi_memory_records(payload)
    except (MemoryStoreUnavailable, ValueError) as exc:
        return {"status": "not_stored", "source": "omi", "type": "conversation", "stored": False, "error": str(exc)}
    return {
        "status": "received",
        "source": "omi",
        "type": "conversation",
        "stored": bool(writes["stored"]),
        "duplicate": bool(writes["duplicates"] and not writes["stored"]),
    }


# Omi realtime transcript webhook
@app.post("/omi/realtime")
def omi_realtime(payload: dict):
    """
    Receives realtime Omi transcript events.
    """
    try:
        writes = store_omi_memory_records(payload)
    except (MemoryStoreUnavailable, ValueError) as exc:
        return {"status": "not_stored", "source": "omi", "type": "realtime", "stored": False, "error": str(exc)}
    return {
        "status": "received",
        "source": "omi",
        "type": "realtime",
        "stored": bool(writes["stored"]),
        "duplicate": bool(writes["duplicates"] and not writes["stored"]),
    }
