import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import backend.qdrant_memory as memory


class _Models:
    class MatchAny:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FieldCondition:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Filter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class RecentOmiMemoryTests(unittest.TestCase):
    def test_recent_omi_memories_filters_and_orders_by_stored_timestamp(self):
        client = Mock()
        client.scroll.return_value = (
            [
                SimpleNamespace(payload={"text": "Older", "type": "omi-summary", "source": "omi:old:summary", "timestamp": "2026-10-14T09:00:00+00:00", "metadata": {"omi_conversation_id": "old"}}),
                SimpleNamespace(payload={"text": "Newer", "type": "omi-transcript", "source": "omi:new:transcript", "timestamp": "2026-10-15T09:00:00+00:00", "metadata": {"omi_conversation_id": "new"}}),
                SimpleNamespace(payload={"text": "Legacy", "type": "fact", "source": "omi", "timestamp": "2026-10-13T09:00:00+00:00", "metadata": {}}),
            ],
            None,
        )
        with patch.object(memory, "models", _Models), patch.object(memory, "init_collection"), patch.object(memory, "get_client", return_value=client):
            records = memory.recent_omi_memories()

        self.assertEqual([record["text"] for record in records], ["Newer", "Older", "Legacy"])
        self.assertEqual(records[0]["metadata"]["omi_conversation_id"], "new")
        self.assertEqual(client.scroll.call_args.kwargs["scroll_filter"].kwargs["should"][0].kwargs["key"], "type")


if __name__ == "__main__":
    unittest.main()
