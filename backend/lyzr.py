import os
import json
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

LYZR_URL = "https://agent-prod.studio.lyzr.ai/v3/inference/chat/"
AGENT_ID = "6ac68b525c854e75d98d34cb"

def ask_lyzr(message, include_managed_agents=False):
    headers = {"Content-Type": "application/json", "x-api-key": os.getenv("LYZR_API_KEY")}
    payload = {
        'user_id': 'hackathon-user',
        'agent_id': AGENT_ID,
        'session_id': 'hackathon-user-' + str(uuid.uuid4()),
        'message': message
    }
    if include_managed_agents:
        payload['managed_agents'] = [
            {'id': '6ac7e667c4e76a0017cbff84', 'name': 'Memory Agent', 'usage_description': 'Retrieve and explain stored facts, personal context, project information, and ideas.'},
            {'id': '6ac7e66ee0c637bc1b676d71', 'name': 'Decision Agent', 'usage_description': 'Retrieve and explain previously recorded decisions and their supporting context.'},
            {'id': '6ac7e67acbf9be6cfdedd266', 'name': 'Task Agent', 'usage_description': 'Retrieve and manage tasks and action items when relevant information is available.'}
        ]
    response = requests.post(LYZR_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    raw = data.get("response", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"intent": "answer", "memory_query": message, "action": raw}


def ask_manager_a2a(message):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": os.getenv("LYZR_API_KEY", "")
    }
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": message}]
            }
        }
    }
    url = "https://agent-prod.studio.lyzr.ai/v3/a2a/serve/6ac68b525c854e75d98d34cb"
    response = requests.post(url, headers=headers, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    artifacts = data.get("result", {}).get("artifacts", [])
    answer = "\n".join(
        part.get("text", "")
        for artifact in artifacts
        for part in artifact.get("parts", [])
        if part.get("kind") == "text"
    ).strip()
    if not answer:
        raise RuntimeError("Lyzr Manager returned no text response")
    return {"intent": "answer", "action": answer}
