import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

load_dotenv()

COLLECTION_NAME = "agent_memory"
VECTOR_SIZE = 384

client = QdrantClient(url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY"))
embedder = SentenceTransformer("all-MiniLM-L6-v2")

def init_collection():
    collections = client.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        client.create_collection(collection_name=COLLECTION_NAME, vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE))

def remember(text, memory_type="fact", source="omi"):
    vector = embedder.encode(text).tolist()
    client.upsert(collection_name=COLLECTION_NAME, points=[models.PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, source + ":" + text)), vector=vector, payload={"text": text, "type": memory_type, "source": source, "timestamp": datetime.now(timezone.utc).isoformat()})])

def recall(query, limit=5):
    vector = embedder.encode(query).tolist()
    results = client.query_points(collection_name=COLLECTION_NAME, query=vector, limit=limit)
    return [{"text": p.payload.get("text"), "type": p.payload.get("type"), "source": p.payload.get("source"), "timestamp": p.payload.get("timestamp"), "score": p.score} for p in results.points]
