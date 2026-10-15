import unittest
from unittest.mock import patch

import backend.main as main
import backend.omi as omi


class OmiTests(unittest.TestCase):
    def test_extracts_nested_transcript(self):
        self.assertEqual(omi.extract_transcript_text({"data": {"segments": [{"text": "First"}, {"text": "Second"}]}}), "First\nSecond")

    def test_sync_reports_duplicates_and_skipped_items(self):
        items = [{"structured": {"title": "Decision", "overview": "Use Qdrant"}}, {"unexpected": "shape"}]
        with patch.object(omi, "get_omi_memories", return_value=items), patch("backend.qdrant_memory.remember", return_value={"stored": False, "duplicate": True}):
            summary = omi.sync_omi_to_qdrant()
        self.assertEqual(summary, {"received": 2, "stored": 0, "duplicates": 1, "skipped": 1})

    def test_records_keep_summary_and_verbatim_transcript_details(self):
        payload = {
            "id": "omi-conversation-1015",
            "source": "phone",
            "structured": {"title": "Planning meeting", "overview": "The team discussed the schedule."},
            "transcript_segments": [{"text": "Mm reference code om i verify ten zoom fifteen."}],
        }
        records = omi.extract_omi_memory_records(payload)
        self.assertEqual(len(records), 2)
        self.assertNotIn("om i verify ten zoom fifteen", records[0]["text"].lower())
        self.assertEqual(records[1]["text"], "Mm reference code om i verify ten zoom fifteen.")
        self.assertEqual(records[1]["source"], "omi:omi-conversation-1015:transcript")
        self.assertEqual(records[1]["metadata"], {
            "omi_conversation_id": "omi-conversation-1015",
            "omi_source": "phone",
            "omi_record_type": "transcript",
        })

    def test_sync_fetches_available_transcript_and_is_duplicate_safe(self):
        listed = {
            "id": "omi-conversation-1015",
            "source": "phone",
            "structured": {"title": "Planning meeting", "overview": "The team discussed the schedule."},
        }
        detailed = {
            **listed,
            "transcript_segments": [{"text": "Mm reference code om i verify ten zoom fifteen."}],
        }
        writes = [
            {"stored": True, "duplicate": False}, {"stored": True, "duplicate": False},
            {"stored": False, "duplicate": True}, {"stored": False, "duplicate": True},
        ]
        with patch.object(omi, "get_omi_memories", return_value=[listed]), patch.object(omi, "get_omi_conversation", return_value=detailed), patch("backend.qdrant_memory.remember", side_effect=writes) as remember:
            first = omi.sync_omi_to_qdrant()
            second = omi.sync_omi_to_qdrant()
        self.assertEqual(first, {"received": 1, "stored": 2, "duplicates": 0, "skipped": 0})
        self.assertEqual(second, {"received": 1, "stored": 0, "duplicates": 2, "skipped": 0})
        transcript_calls = [call.kwargs for call in remember.call_args_list if call.kwargs["memory_type"] == "omi-transcript"]
        self.assertEqual(len(transcript_calls), 2)
        self.assertEqual(transcript_calls[0]["text"], "Mm reference code om i verify ten zoom fifteen.")
        self.assertEqual(transcript_calls[0]["source"], transcript_calls[1]["source"])

    def test_conversation_webhook_stores_summary_and_verbatim_transcript_with_metadata(self):
        payload = {
            "event": {
                "source": "phone",
                "data": {
                    "id": "webhook-conversation-1015",
                    "structured": {"title": "Planning meeting", "overview": "The team discussed the schedule."},
                    "transcript_segments": [{"text": "Mm reference code om i verify ten zoom fifteen."}],
                },
            }
        }
        writes = [{"stored": True, "duplicate": False}, {"stored": True, "duplicate": False}]
        with patch("backend.qdrant_memory.remember", side_effect=writes) as remember:
            response = main.omi_conversation(payload)

        self.assertEqual(response, {"status": "received", "source": "omi", "type": "conversation", "stored": True, "duplicate": False})
        calls = [call.kwargs for call in remember.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["memory_type"], "omi-summary")
        self.assertEqual(calls[0]["source"], "omi:webhook-conversation-1015:summary")
        self.assertEqual(calls[0]["metadata"], {"omi_conversation_id": "webhook-conversation-1015", "omi_source": "phone", "omi_record_type": "summary"})
        self.assertEqual(calls[1]["memory_type"], "omi-transcript")
        self.assertEqual(calls[1]["text"], "Mm reference code om i verify ten zoom fifteen.")
        self.assertEqual(calls[1]["source"], "omi:webhook-conversation-1015:transcript")
        self.assertEqual(calls[1]["metadata"], {"omi_conversation_id": "webhook-conversation-1015", "omi_source": "phone", "omi_record_type": "transcript"})

    def test_repeated_realtime_webhook_is_duplicate_safe_and_missing_payload_is_skipped(self):
        payload = {"data": {"id": "realtime-1015", "source": "phone", "transcript_segments": [{"text": "Original wording only."}]}}
        writes = [
            {"stored": True, "duplicate": False},
            {"stored": False, "duplicate": True},
        ]
        with patch("backend.qdrant_memory.remember", side_effect=writes) as remember:
            first = main.omi_realtime(payload)
            second = main.omi_realtime(payload)
            incomplete = main.omi_realtime({"event": {"data": {}}})

        self.assertEqual(first, {"status": "received", "source": "omi", "type": "realtime", "stored": True, "duplicate": False})
        self.assertEqual(second, {"status": "received", "source": "omi", "type": "realtime", "stored": False, "duplicate": True})
        self.assertEqual(incomplete, {"status": "received", "source": "omi", "type": "realtime", "stored": False, "duplicate": False})
        self.assertEqual(remember.call_count, 2)


if __name__ == "__main__":
    unittest.main()
