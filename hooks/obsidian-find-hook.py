#!/usr/bin/env python3
"""UserPromptSubmit hook: find relevant vault notes via vector similarity.

Falls back to grep if the index DB doesn't exist yet.
"""
import json
import math
import os
import sqlite3
import subprocess
import sys
import urllib.request

VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "/Users/guido.dilauro/WORKDIR/WORK-WIKI")
DB = os.path.expanduser("~/.claude/vault-index.db")
OLLAMA_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"
TOP_K = 3
SNIPPET_CHARS = 400
# Drop results whose final score is below this cosine-equivalent cutoff so
# off-topic prompts inject nothing instead of 5 noisy notes. Override via env.
MIN_SCORE = float(os.environ.get("OBSIDIAN_FIND_MIN_SCORE", "0.60"))
# How many of a note's best chunks to average — more robust than a single chunk.
CHUNKS_PER_NOTE = 2
# Score added when a meaningful query word appears in the note's title/path.
TITLE_BOOST = 0.05


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def embed(text: str) -> list[float] | None:
    try:
        payload = json.dumps({"model": MODEL, "prompt": text}).encode()
        req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())["embedding"]
    except Exception:
        return None


def query_terms(prompt: str) -> set[str]:
    return {w.lower() for w in prompt.split() if len(w) >= 3}


def vector_search(prompt: str) -> list[tuple[str, str]]:
    qemb = embed(prompt)
    if qemb is None:
        return []

    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT path, chunk_text, embedding FROM embeddings").fetchall()
    conn.close()

    per_note: dict[str, list[tuple[float, str]]] = {}
    for path, chunk_text, emb_json in rows:
        score = cosine(qemb, json.loads(emb_json))
        per_note.setdefault(path, []).append((score, chunk_text))

    terms = query_terms(prompt)
    scored: dict[str, tuple[float, str]] = {}
    for path, chunks in per_note.items():
        chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = chunks[:CHUNKS_PER_NOTE]
        agg = sum(s for s, _ in top_chunks) / len(top_chunks)
        if terms and any(t in path.lower() for t in terms):
            agg += TITLE_BOOST
        scored[path] = (agg, top_chunks[0][1])

    ranked = sorted(scored.items(), key=lambda x: x[1][0], reverse=True)
    return [
        (path, data[1][:SNIPPET_CHARS])
        for path, data in ranked[:TOP_K]
        if data[0] >= MIN_SCORE
    ]


def grep_fallback(prompt: str) -> list[tuple[str, str]]:
    stopwords = {'what','when','where','which','while','about','after','before','there','their',
                 'would','could','should','these','those','check','using','being','doing','going',
                 'getting','making','taking','having','looking','working','trying','think','know',
                 'want','need','have','will','with','from','that','this','into','then','than',
                 'just','also','been','were','them','some','your','does','will','tell','help'}
    words = [w.lower() for w in prompt.split() if len(w) >= 3 and w.lower() not in stopwords]
    query = '|'.join(words[:5]) if words else ''
    if not query:
        return []
    try:
        out = subprocess.run(
            ['grep', '-ril', '--include=*.md', '-E', query, VAULT],
            capture_output=True, text=True, timeout=5
        )
        files = [f for f in out.stdout.strip().split('\n') if f][:TOP_K]
        results = []
        for f in files:
            rel = f.replace(VAULT + '/', '')
            snippet = subprocess.run(
                ['grep', '-im', '10', '-E', query, f],
                capture_output=True, text=True, timeout=3
            ).stdout.strip()[:SNIPPET_CHARS]
            results.append((rel, snippet))
        return results
    except Exception:
        return []


def main() -> None:
    data = json.load(sys.stdin)
    prompt = data.get('prompt', '')
    if not prompt:
        return

    use_vector = os.path.exists(DB)
    results = vector_search(prompt) if use_vector else grep_fallback(prompt)

    if not results:
        return

    lines = [f'[[{path}]]: {snippet}' for path, snippet in results]
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': ('Relevant wiki notes (vector search):\n' if use_vector else 'Relevant wiki notes:\n') + '\n'.join(lines)
        }
    }))


if __name__ == "__main__":
    main()
