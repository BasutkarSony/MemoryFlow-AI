from backend.qdrant_memory import recall
from backend.agent_core import filter_relevant_memories

cases = [
    ("demo code", "What is MemoryFlow's demo code?", [["demo code is mf-7319"]]),
    ("product name", "product name chosen for the hackathon", [["chosen product name is memoryflow"]]),
    ("persistent memory", "what did we decide to use for persistent memory", [["we decided to use qdrant", "persistent memory is qdrant"]]),
]
k = 5
returned_total = relevant_total = 0
coverage = []
rrs = []

print("MEMORYFLOW RETRIEVAL EVALUATION")
for name, query, expected_groups in cases:
    raw = recall(query, limit=k)
    got = filter_relevant_memories(query, raw)[:k]
    returned_total += len(got)

    def matches(text, group):
        text = text.lower()
        return any(pattern in text for pattern in group)

    relevant = [item for item in got if any(matches(item.get("text", ""), group) for group in expected_groups)]
    relevant_total += len(relevant)
    found = sum(any(matches(item.get("text", ""), group) for item in got) for group in expected_groups)
    coverage.append(found / max(1, len(expected_groups)))
    rank = next((i for i, item in enumerate(got, 1) if any(matches(item.get("text", ""), group) for group in expected_groups)), None)
    rrs.append(1 / rank if rank else 0)

    print(f"\n{name}: returned={len(got)}, relevant={len(relevant)}, first_target_rank={rank or 'MISS'}")
    for i, item in enumerate(got, 1):
        print(f"{i}. {item.get('text', '')}")

print("\nMETRICS")
print(f"Precision@{k}: {relevant_total / max(1, returned_total):.3f}")
print(f"Target coverage@{k}: {sum(coverage) / len(cases):.3f}")
print(f"MRR@{k}: {sum(rrs) / len(cases):.3f}")
print("Small manually labeled test set; expand it before treating these as benchmark metrics.")
