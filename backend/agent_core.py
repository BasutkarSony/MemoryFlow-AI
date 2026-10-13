from backend.lyzr import ask_lyzr, ask_manager_a2a
from backend.qdrant_memory import init_collection, remember, recall


def filter_relevant_memories(query, results, min_score=0.40):
    import re

    stop_words = {
        "what", "when", "where", "who", "why", "how", "did", "does", "do",
        "is", "are", "was", "were", "the", "a", "an", "my", "our", "we",
        "i", "you", "me", "about", "tell", "please", "remember", "previously",
        "have", "has", "had", "can", "could", "would", "should", "to", "for",
        "of", "in", "on", "at", "it", "that", "this", "and", "or", "with"
    }

    def terms(value):
        return {
            token for token in re.findall(r"[a-z0-9]+", str(value).lower())
            if len(token) > 1 and token not in stop_words
        }

    query_terms = terms(query)
    ranked = []
    for item in results:
        score = float(item.get("score") or 0)
        memory_terms = terms(item.get("text", ""))
        overlap = len(query_terms & memory_terms)
        attribute_match = not ("demo" in query_terms and "code" in query_terms) or ("demo" in memory_terms and "code" in memory_terms)
        if attribute_match and (('demo' in query_terms and 'code' in query_terms and 'demo' in memory_terms and 'code' in memory_terms) or score >= 0.78 or (overlap >= 2 or (score >= min_score and overlap >= min(1, len(query_terms))))):
            copy = dict(item)
            copy["_relevance"] = overlap
            ranked.append(copy)

    ranked.sort(key=lambda item: (item["_relevance"], float(item.get("score") or 0)), reverse=True)
    if "product" in query_terms and "name" in query_terms:
        ranked = [item for item in ranked if "code word" not in str(item.get("text", "")).lower()] + [item for item in ranked if "code word" in str(item.get("text", "")).lower()][:0]
    unique = []
    seen = set()
    for item in ranked:
        import re
        key = " ".join(re.findall(r"[a-z0-9]+", str(item.get("text", "")).lower()))
        if key not in seen:
            seen.add(key)
            item.pop("_relevance", None)
            unique.append(item)
    return unique


def process_input(text):
    text_lower = text.lower()
    memory_cues = (
        'what did we decide', 'what did i decide', 'what was the decision',
        'previously recorded', 'remembered decision', 'do you remember',
        'what did we choose', 'what did we use for persistent memory',
        'what did we discuss', 'what did we agree',
        'what is the demo code', 'what is the project demo code', 'what is memoryflow', 'what was memoryflow',
        'what is the code', 'what was the code', 'what is the project code',
        'what do you remember about', 'what did i ask you to remember',
        'what have i asked you to remember', 'what was the ',
        'what is my ', 'what is our ', 'what was my ', 'what was our '
    )
    remember_prefixes = ('remember that ', 'remember ', 'save that ', 'save ', 'store that ', 'store ', 'note that ', 'note ')
    if any(text_lower.startswith(prefix) for prefix in remember_prefixes):
        memory_text = text.strip()
        for prefix in remember_prefixes:
            if memory_text.lower().startswith(prefix):
                memory_text = memory_text[len(prefix):].strip()
                break
        decision = {"intent": "remember", "memory_query": memory_text}
    elif any(cue in text_lower for cue in memory_cues) or ("memoryflow" in text_lower and any(word in text_lower for word in ("decide", "decision", "database", "qdrant", "pinecone", "integration", "reference", "demo", "code", "product", "project", "test", "memory", "omi", "lyzr"))) or (text_lower.startswith(("what is ", "what was ", "what are ", "which ", "where is ", "when did ", "who is ")) and any(word in text_lower for word in ("memoryflow", "memory", "qdrant", "omi", "lyzr", "integration test"))):
        decision = {"intent": "recall", "memory_query": text}
    else:
        decision = ask_manager_a2a(text)
    intent=decision.get("intent","answer")
    query=decision.get("memory_query") or text
    if intent=="remember":
        remember(query,memory_type="fact",source="lyzr")
        return {"intent":"remember","message":"Memory stored successfully.","memory":query}
    if intent=="recall":
        results=filter_relevant_memories(query, recall(query))
        if not results:
            return {"intent":"recall","answer":"I could not find a relevant memory.","results":[]}
        memory_text="\n".join(["- "+item["text"] for item in results])
        prompt=(
            "You are answering a memory-retrieval request. Do not reclassify it. "
            "Use ONLY the supplied retrieved memories as evidence. Do not claim to have searched memory yourself. "
            "If the context does not answer the question, say so clearly. Do not invent facts. "
            "If retrieved memories directly disagree about the same attribute, state the conflict instead of guessing. "
            "Do not treat different attributes (for example, a demo code and a separate code word) as a conflict. "
            "Return only a concise natural-language answer.\n\n"
            f"Original user question: {text}\n\n"
            f"Retrieved memories from the application's memory store:\n{memory_text}\n"
        )
        final=ask_lyzr(prompt)
        answer=final.get("action") or final.get("answer") or final.get("memory_query") or str(final)
        return {"intent":"recall","answer":answer,"results":results}
    return {"intent":intent,"action":decision.get("action",""),"memory_query":query}

init_collection()
