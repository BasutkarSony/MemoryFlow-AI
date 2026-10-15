import os
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import requests
from backend.lyzr import LyzrUnavailable, ask_manager_a2a


class LyzrA2ATests(unittest.TestCase):
    def _response(self, payload, status_code=200):
        response = Mock(status_code=status_code)
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response

    def test_parses_text_parts_inside_task_artifacts(self):
        payload = {"jsonrpc": "2.0", "result": {"artifacts": [{"parts": [{"kind": "text", "text": "Grounded answer."}]}]}}
        with patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", return_value=self._response(payload)):
            result = ask_manager_a2a("ignored")
        self.assertEqual(result["answer"], "Grounded answer.")
        self.assertEqual(result["diagnostics"]["category"], "success")
        self.assertEqual(result["diagnostics"]["artifact_count"], 1)

    def test_parses_direct_message_parts(self):
        payload = {"jsonrpc": "2.0", "result": {"role": "agent", "parts": [{"kind": "text", "text": "Direct reply."}]}}
        with patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", return_value=self._response(payload)):
            result = ask_manager_a2a("ignored")
        self.assertEqual(result["answer"], "Direct reply.")

    def test_reports_explicit_delegation_and_return_separately(self):
        payload = {
            "jsonrpc": "2.0",
            "result": {
                "status": {"state": "completed"},
                "history": [{"role": "user", "parts": []}, {"role": "agent", "parts": []}],
                "artifacts": [{
                    "metadata": {"delegations": [{"agent_name": "Decision Agent", "state": "completed"}]},
                    "parts": [{"kind": "text", "text": "Grounded answer."}],
                }],
            },
        }
        with patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", return_value=self._response(payload)):
            result = ask_manager_a2a("ignored")
        self.assertEqual(result["orchestration"]["verification_state"], "delegated_result_verified")
        self.assertEqual(result["orchestration"]["invoked_agents"], ["Decision Agent"])
        self.assertEqual(result["orchestration"]["returned_agents"], ["Decision Agent"])
        self.assertEqual(result["task_observability"], {"task_state": "completed", "history_roles": ["user", "agent"], "artifact_metadata_keys": [["delegations"]]})

    def test_does_not_treat_a_manager_answer_as_delegation(self):
        payload = {"jsonrpc": "2.0", "result": {"agent_id": "manager-only", "parts": [{"kind": "text", "text": "Manager answer."}]}}
        with patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", return_value=self._response(payload)):
            result = ask_manager_a2a("ignored")
        self.assertEqual(result["orchestration"]["verification_state"], "orchestration_not_verified")
        self.assertFalse(result["orchestration"]["delegation_invoked_verified"])

    def test_json_rpc_error_is_explicit_and_safe(self):
        payload = {"jsonrpc": "2.0", "error": {"code": -32602, "message": "details intentionally not surfaced"}}
        with patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", return_value=self._response(payload)):
            with self.assertRaises(LyzrUnavailable) as captured:
                ask_manager_a2a("ignored")
        self.assertEqual(captured.exception.diagnostics["category"], "json_rpc_error")
        self.assertEqual(captured.exception.diagnostics["json_rpc_error_code"], -32602)
        self.assertNotIn("details intentionally not surfaced", str(captured.exception.diagnostics))

    def test_empty_task_response_is_reported_with_shape_only(self):
        payload = {"jsonrpc": "2.0", "result": {"artifacts": [{"parts": []}]}}
        with patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", return_value=self._response(payload)):
            with self.assertRaises(LyzrUnavailable) as captured:
                ask_manager_a2a("ignored")
        self.assertEqual(captured.exception.diagnostics["category"], "empty_response")
        self.assertEqual(captured.exception.diagnostics["artifact_count"], 1)

    def test_transport_failures_are_classified_without_exception_text(self):
        dns_error = requests.exceptions.ConnectionError("x-api-key=secret-value")
        dns_error.__cause__ = socket.gaierror("resolution detail")
        permission_error = requests.exceptions.ConnectionError("network detail secret-value")
        permission_error.__cause__ = PermissionError(13, "permission detail")
        cases = [
            (dns_error, "dns_resolution_failure"),
            (permission_error, "network_permission_denied"),
            (requests.exceptions.ProxyError("proxy password=secret-value"), "proxy_error"),
            (requests.exceptions.SSLError(ssl.SSLError("certificate detail secret-value")), "tls_ssl_error"),
            (requests.exceptions.ConnectTimeout("connect detail secret-value"), "connect_timeout"),
            (requests.exceptions.ReadTimeout("read detail secret-value"), "read_timeout"),
        ]
        for error, expected_category in cases:
            with self.subTest(category=expected_category), patch.dict(os.environ, {"LYZR_API_KEY": "test-key"}), patch("backend.lyzr.requests.post", side_effect=error):
                with self.assertRaises(LyzrUnavailable) as captured:
                    ask_manager_a2a("ignored")
            diagnostics = captured.exception.diagnostics
            self.assertEqual(diagnostics["category"], expected_category)
            self.assertIn("exception_class", diagnostics)
            self.assertNotIn("secret-value", str(diagnostics))


if __name__ == "__main__":
    unittest.main()
