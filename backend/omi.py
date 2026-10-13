import os
import requests
from dotenv import load_dotenv

load_dotenv()

OMI_URL="https://api.omi.me/v1/dev/user/conversations"

def get_omi_memories(limit=20):
    headers={"Authorization":"Bearer "+os.getenv("OMI_API_KEY","")}
    response=requests.get(OMI_URL,headers=headers,params=dict(limit=limit),timeout=30)
    response.raise_for_status()
    data=response.json()
    if isinstance(data,list):
        return data[:limit]
    if isinstance(data,dict):
        return data.get("memories",data.get("items",[]))[:limit]
    return []

def get_memory_texts(limit=20):
    texts = []
    for item in get_omi_memories(limit):
        if isinstance(item, dict):
            d = item.get("structured") or {}
            text = chr(10).join(str(d.get(k) or "").strip() for k in ("title", "overview") if str(d.get(k) or "").strip())
            if text: texts.append(text)
    return texts

def sync_omi_to_qdrant():
    from backend.qdrant_memory import remember
    memories=get_memory_texts()
    for text in memories:
        remember(text,memory_type="fact",source="omi")
    return len(memories)

def extract_transcript_text(payload):
    # Extract transcript text from common Omi webhook payload shapes.
    text_keys = ("text", "transcript", "transcription", "utterance")
    containers = ("segments", "transcript_segments", "utterances", "messages", "conversation", "data", "payload", "event")
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        parts = [extract_transcript_text(item) for item in payload]
        return chr(10).join(part for part in parts if part).strip()
    if not isinstance(payload, dict):
        return ""
    for key in text_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in containers:
        if key in payload:
            value = extract_transcript_text(payload[key])
            if value:
                return value
    return ""

