"""Qdrant-backed memory storage with lazy, failure-aware clients."""

import importlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
try:
    from qdrant_client import QdrantClient, models
except ImportError:  # Keep the API health endpoint usable before dependencies are installed.
    QdrantClient = None
    models = None

load_dotenv()

COLLECTION_NAME = "agent_memory"
VECTOR_SIZE = 384


class MemoryStoreUnavailable(RuntimeError):
    """Raised when persistent memory cannot be reached safely."""


_client: Any = None
_embedder: Any = None
_payload_indexes_ready = False


def get_client() -> Any:
    global _client
    if QdrantClient is None:
        raise MemoryStoreUnavailable("Qdrant support is not installed.")
    if _client is None:
        url = os.getenv("QDRANT_URL")
        if not url or url.startswith("replace_with_"):
            raise MemoryStoreUnavailable("Qdrant is not configured.")
        _client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None, timeout=15)
    return _client


def get_embedder() -> Any:
    global _embedder
    if _embedder is None:
        try:
            sentence_transformers = importlib.import_module("sentence_transformers")
            _embedder = sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as exc:
            raise MemoryStoreUnavailable("The memory embedding model is unavailable.") from exc
    return _embedder


def _raise_unavailable(exc: Exception) -> None:
    if isinstance(exc, MemoryStoreUnavailable):
        raise exc
    raise MemoryStoreUnavailable("Qdrant is unavailable; no memory was changed.") from exc


def init_collection() -> None:
    global _payload_indexes_ready
    """Create the collection once, preserving any existing memories."""
    try:
        client = get_client()
        collections = client.get_collections().collections
        if not any(collection.name == COLLECTION_NAME for collection in collections):
            if models is None:
                raise MemoryStoreUnavailable("Qdrant support is not installed.")
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
            )
        if not _payload_indexes_ready and models is not None and hasattr(models, 'PayloadSchemaType') and hasattr(client, 'create_payload_index'):
            for field in ('type', 'source'):
                client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            _payload_indexes_ready = True
    except Exception as exc:
        _raise_unavailable(exc)


def _memory_id(text: str, source: str) -> str:
    normalized = " ".join(text.split()).casefold()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{normalized}"))


def remember(text: str, memory_type: str = "fact", source: str = "user", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist one memory, preserving its wording while deduplicating normalized text."""
    original_text = str(text).strip()
    normalized = " ".join(original_text.split())
    if not original_text:
        raise ValueError("Cannot save an empty memory.")
    try:
        init_collection()
        client = get_client()
        point_id = _memory_id(normalized, source)
        if client.retrieve(COLLECTION_NAME, ids=[point_id], with_payload=False):
            return {"id": point_id, "stored": False, "duplicate": True}
        vector = get_embedder().encode(original_text).tolist()
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "text": original_text,
                    "type": memory_type,
                    "source": source,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "metadata": metadata or {},
                },
            )],
        )
        return {"id": point_id, "stored": True, "duplicate": False}
    except ValueError:
        raise
    except Exception as exc:
        _raise_unavailable(exc)


def recall(query: str, limit: int = 5) -> list[dict[str, Any]]:
    normalized = " ".join(str(query).split())
    if not normalized:
        return []
    try:
        init_collection()
        vector = get_embedder().encode(normalized).tolist()
        points = get_client().query_points(
            collection_name=COLLECTION_NAME, query=vector, limit=limit, with_payload=True
        ).points
        return [
            {
                "text": point.payload.get("text", ""),
                "type": point.payload.get("type", "fact"),
                "source": point.payload.get("source", "memory"),
                "timestamp": point.payload.get("timestamp"),
                "score": point.score,
                "metadata": point.payload.get("metadata", {}),
            }
            for point in points
        ]
    except Exception as exc:
        _raise_unavailable(exc)


def _memory_record(point: Any) -> dict[str, Any]:
    payload = point.payload or {}
    return {
        "text": payload.get("text", ""),
        "type": payload.get("type", "fact"),
        "source": payload.get("source", "memory"),
        "timestamp": payload.get("timestamp"),
        "metadata": payload.get("metadata", {}),
    }


def _is_omi_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").casefold()
    return record.get("type") in {"omi-summary", "omi-transcript"} or source == "omi" or source.startswith("omi-") or source.startswith("omi:")


def recent_omi_memories(limit: int = 50, scan_limit: int = 500) -> list[dict[str, Any]]:
    """Return recent Omi records by their stored timestamp, not semantic similarity.

    The bounded scan keeps discovery safe for a growing collection.  Records are
    sorted after retrieval because the collection has no timestamp payload index.
    """
    if limit < 1:
        return []
    try:
        init_collection()
        if models is None:
            raise MemoryStoreUnavailable("Qdrant support is not installed.")
        record_filter = models.Filter(
            should=[
                models.FieldCondition(key="type", match=models.MatchAny(any=["omi-summary", "omi-transcript"])),
                models.FieldCondition(key="source", match=models.MatchAny(any=["omi", "omi-webhook", "omi-realtime"])),
            ]
        )
        records: list[dict[str, Any]] = []
        offset = None
        while len(records) < scan_limit:
            batch_size = min(100, scan_limit - len(records))
            points, offset = get_client().scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=record_filter,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            records.extend(record for point in points if _is_omi_record(record := _memory_record(point)))
            if offset is None or not points:
                break
        records.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return records[:limit]
    except Exception as exc:
        _raise_unavailable(exc)


def connection_status() -> str:
    try:
        init_collection()
        return "connected"
    except MemoryStoreUnavailable:
        return "disconnected"
