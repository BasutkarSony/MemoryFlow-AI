"""Omi ingestion helpers with bounded network calls and transcript preservation."""

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

OMI_URL = "https://api.omi.me/v1/dev/user/conversations"


class OmiUnavailable(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    api_key = os.getenv("OMI_API_KEY", "")
    if not api_key or api_key.startswith("replace_with_"):
        raise OmiUnavailable("Omi is not configured.")
    return {"Authorization": f"Bearer {api_key}"}


def get_omi_memories(limit: int = 20) -> list[dict[str, Any]]:
    try:
        response = requests.get(OMI_URL, headers=_headers(), params={"limit": limit}, timeout=20)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OmiUnavailable("Omi could not be reached; no conversations were imported.") from exc
    items = data if isinstance(data, list) else data.get("memories", data.get("items", [])) if isinstance(data, dict) else []
    return [item for item in items[:limit] if isinstance(item, dict)]


def get_omi_conversation(conversation_id: str) -> dict[str, Any]:
    """Fetch full segments only when a listed conversation omitted them."""
    try:
        response = requests.get(
            f"{OMI_URL}/{conversation_id}", headers=_headers(), params={"include_transcript": "true"}, timeout=20
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OmiUnavailable("Omi conversation transcript could not be retrieved.") from exc
    return data if isinstance(data, dict) else {}


def _structured_text(payload: Any) -> str:
    structured = payload.get("structured") if isinstance(payload, dict) else None
    if not isinstance(structured, dict):
        return ""
    return "\n".join(str(structured[key]).strip() for key in ("title", "overview") if isinstance(structured.get(key), str) and structured[key].strip())


def _segment_text(value: Any) -> str:
    if isinstance(value, list):
        parts = [_segment_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    return value.strip() if isinstance(value, str) else ""


def extract_transcript_text(payload: Any) -> str:
    """Extract available transcript wording without interpreting or correcting it."""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        return _segment_text(payload)
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "transcript", "transcription", "utterance", "transcript_segments", "segments", "utterances", "messages"):
        text = _segment_text(payload.get(key))
        if text:
            return text
    for key in ("conversation", "data", "payload", "event"):
        text = extract_transcript_text(payload.get(key))
        if text:
            return text
    return ""


def _conversation_payload(payload: Any) -> dict[str, Any]:
    """Unwrap supported webhook envelopes without inventing missing conversation data."""
    if not isinstance(payload, dict):
        return {}
    content_fields = {"structured", "text", "transcript", "transcription", "utterance", "transcript_segments", "segments", "utterances", "messages"}
    if content_fields & payload.keys():
        return payload
    for key in ("conversation", "data", "payload", "event"):
        nested = _conversation_payload(payload.get(key))
        if nested:
            # Some webhook envelopes carry the ID/source outside the conversation body.
            return {**{field: payload[field] for field in ("id", "source") if field in payload}, **nested}
    if "id" in payload or "source" in payload:
        return payload
    return {}


def extract_omi_memory_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return separate summary/transcript records when both are available."""
    payload = _conversation_payload(payload)
    if not payload:
        return []
    conversation_id = str(payload.get("id") or "unknown")
    source_value = payload.get("source")
    provenance = {"omi_conversation_id": conversation_id, "omi_source": source_value if isinstance(source_value, str) else None}
    records: list[dict[str, Any]] = []
    summary = _structured_text(payload)
    transcript = extract_transcript_text(payload)
    if summary:
        records.append({"text": summary, "memory_type": "omi-summary", "source": f"omi:{conversation_id}:summary", "metadata": {**provenance, "omi_record_type": "summary"}})
    if transcript and " ".join(transcript.split()).casefold() != " ".join(summary.split()).casefold():
        records.append({"text": transcript, "memory_type": "omi-transcript", "source": f"omi:{conversation_id}:transcript", "metadata": {**provenance, "omi_record_type": "transcript"}})
    return records


def store_omi_memory_records(payload: dict[str, Any]) -> dict[str, int]:
    """Persist shared Omi summary/transcript records and report duplicate-safe writes."""
    from backend.qdrant_memory import remember

    records = extract_omi_memory_records(payload)
    summary = {"stored": 0, "duplicates": 0, "skipped": 0}
    if not records:
        summary["skipped"] = 1
        return summary
    for record in records:
        result = remember(**record)
        summary["stored"] += int(result["stored"])
        summary["duplicates"] += int(result["duplicate"])
    return summary


def sync_omi_to_qdrant(limit: int = 20) -> dict[str, int]:
    summary = {"received": 0, "stored": 0, "duplicates": 0, "skipped": 0}
    for listed_item in get_omi_memories(limit):
        summary["received"] += 1
        item = listed_item
        # The Omi detail API is the documented way to request transcript segments.
        if not extract_transcript_text(item) and isinstance(item.get("id"), str) and item["id"]:
            try:
                detailed = get_omi_conversation(item["id"])
                if detailed:
                    item = {**listed_item, **detailed}
            except OmiUnavailable:
                pass  # Summary-only ingestion remains useful when detail fetch is unavailable.
        writes = store_omi_memory_records(item)
        summary["stored"] += writes["stored"]
        summary["duplicates"] += writes["duplicates"]
        summary["skipped"] += writes["skipped"]
    return summary
