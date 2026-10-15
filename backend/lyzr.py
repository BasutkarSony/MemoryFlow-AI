"""Lyzr manager calls used for reasoning over already-retrieved evidence."""

import os
import errno
import socket
import ssl
import uuid
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

AGENT_ID = "6ac68b525c854e75d98d34cb"
MANAGER_URL = f"https://agent-prod.studio.lyzr.ai/v3/a2a/serve/{AGENT_ID}"


class LyzrUnavailable(RuntimeError):
    """A safe Lyzr failure with metadata suitable for UI/API diagnostics."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {"category": "unknown"}


def _response_shape(data: Any, status_code: int | None, category: str) -> dict[str, Any]:
    """Expose response structure only; never include prompts, keys, or response text."""
    diagnostics: dict[str, Any] = {"category": category, "http_status": status_code}
    if isinstance(data, dict):
        diagnostics["top_level_keys"] = sorted(str(key) for key in data.keys())
        result = data.get("result")
        if isinstance(result, dict):
            diagnostics["result_keys"] = sorted(str(key) for key in result.keys())
            artifacts = result.get("artifacts")
            if isinstance(artifacts, list):
                diagnostics["artifact_count"] = len(artifacts)
    else:
        diagnostics["response_type"] = type(data).__name__
    return diagnostics


def _task_observability(result: Any) -> dict[str, Any]:
    """Return safe A2A task structure, never message/artifact content or metadata values."""
    if not isinstance(result, dict):
        return {"task_state": None, "history_roles": [], "artifact_metadata_keys": []}
    status = result.get("status")
    task_state = status.get("state") if isinstance(status, dict) and isinstance(status.get("state"), str) else None
    history = result.get("history", [])
    history_roles = [entry.get("role") for entry in history if isinstance(entry, dict) and isinstance(entry.get("role"), str)] if isinstance(history, list) else []
    artifacts = result.get("artifacts", [])
    metadata_keys = []
    if isinstance(artifacts, list):
        for artifact in artifacts:
            metadata = artifact.get("metadata") if isinstance(artifact, dict) else None
            if isinstance(metadata, dict):
                metadata_keys.append(sorted(str(key) for key in metadata.keys()))
    return {"task_state": task_state, "history_roles": history_roles, "artifact_metadata_keys": metadata_keys}


def _delegation_records(value: Any) -> list[dict[str, Any]]:
    """Extract only explicitly labelled delegation records from supported A2A containers."""
    if isinstance(value, list):
        return [record for item in value for record in _delegation_records(item)]
    if not isinstance(value, dict):
        return []
    records: list[dict[str, Any]] = []
    for key in ("delegated_agents", "delegations", "sub_agents", "delegation"):
        child = value.get(key)
        entries = child if isinstance(child, list) else [child]
        for entry in entries:
            if isinstance(entry, str) and entry.strip():
                records.append({"agent": entry.strip(), "returned": False})
            elif isinstance(entry, dict):
                agent = entry.get("agent_name") or entry.get("agentName") or entry.get("agent_id") or entry.get("agentId")
                if isinstance(agent, str) and agent.strip():
                    state = entry.get("state") or entry.get("status")
                    returned = (
                        isinstance(state, str) and state.lower() in {"completed", "complete", "succeeded", "success", "returned"}
                    ) or any(field in entry for field in ("result", "output", "response", "artifacts"))
                    records.append({"agent": agent.strip(), "returned": returned})
    for key in ("artifacts", "artifact", "parts", "message", "status", "history", "metadata"):
        if key in value:
            records.extend(_delegation_records(value[key]))
    return records


def _orchestration_observability(result: Any, manager_responded: bool) -> dict[str, Any]:
    records = _delegation_records(result)
    invoked = list(dict.fromkeys(record["agent"] for record in records))
    returned = list(dict.fromkeys(record["agent"] for record in records if record["returned"]))
    return {
        "manager_responded": manager_responded,
        "delegation_invoked_verified": bool(invoked),
        "delegated_result_verified": bool(returned),
        "invoked_agents": invoked,
        "returned_agents": returned,
        "verification_state": "delegated_result_verified" if returned else "delegation_invoked_verified" if invoked else "orchestration_not_verified",
    }


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Walk wrapped request causes without reading their potentially sensitive text."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: Any = exc
    while isinstance(current, BaseException) and id(current) not in seen and len(chain) < 8:
        chain.append(current)
        seen.add(id(current))
        next_error = current.__cause__ or current.__context__
        if next_error is None:
            next_error = getattr(current, "reason", None) or getattr(current, "original_error", None)
        current = next_error
    return chain


def _transport_diagnostics(exc: requests.RequestException, response: Any = None) -> dict[str, Any]:
    """Classify Requests/urllib3/socket failures without exposing exception text."""
    chain = _exception_chain(exc)
    classes = [type(error).__name__ for error in chain]
    status_response = response if response is not None else getattr(exc, "response", None)
    status_code = getattr(status_response, "status_code", None)
    proxy_names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    diagnostics: dict[str, Any] = {
        "category": "request_error",
        "exception_class": type(exc).__name__,
        "exception_chain": classes,
        "http_status": status_code,
        "endpoint_scheme": "https",
        "endpoint_host": "agent-prod.studio.lyzr.ai",
        "proxy_environment_present": any(bool(os.getenv(name)) for name in proxy_names),
    }
    if any(isinstance(error, socket.gaierror) or type(error).__name__ == "NameResolutionError" for error in chain):
        diagnostics["category"] = "dns_resolution_failure"
    elif any(isinstance(error, requests.exceptions.ProxyError) or type(error).__name__ == "ProxyError" for error in chain):
        diagnostics["category"] = "proxy_error"
    elif any(isinstance(error, (requests.exceptions.SSLError, ssl.SSLError)) or type(error).__name__ in {"SSLError", "SSLCertVerificationError"} for error in chain):
        diagnostics["category"] = "tls_ssl_error"
    elif any(isinstance(error, requests.exceptions.ConnectTimeout) or type(error).__name__ == "ConnectTimeoutError" for error in chain):
        diagnostics["category"] = "connect_timeout"
    elif any(isinstance(error, requests.exceptions.ReadTimeout) or type(error).__name__ == "ReadTimeoutError" for error in chain):
        diagnostics["category"] = "read_timeout"
    elif any(isinstance(error, requests.exceptions.Timeout) for error in chain):
        diagnostics["category"] = "request_timeout"
    elif any(isinstance(error, PermissionError) for error in chain):
        diagnostics["category"] = "network_permission_denied"
    elif any(isinstance(error, ConnectionRefusedError) or getattr(error, "errno", None) in {errno.ECONNREFUSED, 10061} for error in chain):
        diagnostics["category"] = "connection_refused"
    elif any(isinstance(error, requests.exceptions.ConnectionError) or type(error).__name__ in {"NewConnectionError", "MaxRetryError"} for error in chain):
        diagnostics["category"] = "connection_error"
    elif isinstance(exc, requests.exceptions.HTTPError):
        diagnostics["category"] = "http_error"
    return diagnostics


def _text_parts(value: Any) -> list[str]:
    """Extract text from A2A TextParts inside supported Message/Task containers."""
    if isinstance(value, list):
        return [text for item in value for text in _text_parts(item)]
    if not isinstance(value, dict):
        return []
    text = value.get("text")
    found = [text.strip()] if isinstance(text, str) and text.strip() else []
    # A2A messages and artifacts carry text in their parts; tasks carry artifacts
    # and may expose a status message while the task is still in progress.
    for key in ("parts", "artifacts", "artifact"):
        if key in value:
            found.extend(_text_parts(value[key]))
    status = value.get("status")
    if isinstance(status, dict):
        found.extend(_text_parts(status.get("message")))
    message = value.get("message")
    if isinstance(message, dict):
        found.extend(_text_parts(message))
    return found


def _delegations(value: Any) -> list[str]:
    """Only report delegation that Lyzr explicitly returned in a delegation field."""
    return list(dict.fromkeys(record["agent"] for record in _delegation_records(value)))


def ask_manager_a2a(message: str) -> dict[str, Any]:
    """Ask the configured Lyzr manager and parse A2A Task or Message output."""
    api_key = os.getenv("LYZR_API_KEY", "")
    if not api_key or api_key.startswith("replace_with_"):
        raise LyzrUnavailable("Lyzr is not configured.", {"category": "not_configured"})
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {"message": {
            "role": "user", "kind": "message", "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": message}],
        }},
    }
    response = None
    try:
        response = requests.post(
            MANAGER_URL,
            headers={"Accept": "application/json", "Content-Type": "application/json", "x-api-key": api_key},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LyzrUnavailable("Lyzr reasoning is unavailable.", _transport_diagnostics(exc, response)) from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise LyzrUnavailable("Lyzr returned a non-JSON response.", {"category": "invalid_json", "http_status": response.status_code}) from exc
    if not isinstance(data, dict):
        raise LyzrUnavailable("Lyzr returned an invalid JSON-RPC response.", _response_shape(data, response.status_code, "invalid_response"))
    if data.get("error") is not None:
        diagnostics = _response_shape(data, response.status_code, "json_rpc_error")
        error = data["error"]
        if isinstance(error, dict) and "code" in error:
            diagnostics["json_rpc_error_code"] = error["code"]
        raise LyzrUnavailable("Lyzr returned a JSON-RPC error.", diagnostics)
    if "result" not in data:
        raise LyzrUnavailable("Lyzr returned no JSON-RPC result.", _response_shape(data, response.status_code, "missing_result"))
    result = data["result"]
    answer = "\n".join(_text_parts(result)).strip()
    if not answer:
        raise LyzrUnavailable("Lyzr returned no text response.", _response_shape(data, response.status_code, "empty_response"))
    delegated_agents = list(dict.fromkeys(_delegations(result)))
    orchestration = _orchestration_observability(result, manager_responded=True)
    return {
        "answer": answer,
        "delegated_agents": delegated_agents,
        "delegation_verified": bool(delegated_agents),
        "orchestration": orchestration,
        "task_observability": _task_observability(result),
        "diagnostics": _response_shape(data, response.status_code, "success"),
    }
