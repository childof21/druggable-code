#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build data/vectors.bin + data/chunks.json for the Druggable Code blog RAG chatbot.

Embeddings: openai/text-embedding-3-large (3072 dims) via the LOCAL embed service
(http://127.0.0.1:8501/embed — proxies to OpenRouter; key stays on this server).
Chunks: one per h3 section (~1600 chars cap) prefixed with post title + heading.
Re-run whenever posts.json changes (bot does this automatically after each publish).
"""
import json, os, struct, urllib.request

PROJ = os.path.expanduser("~/druggable-code-bot")
POSTS = f"{PROJ}/posts.json"
EMBED_URL = "http://127.0.0.1:8501/embed"
TOKEN = open(os.path.expanduser("~/embed-svc/.env")).read().split("EMBED_TOKEN=")[1].split("\n")[0]


def embed_batch(texts):
    body = json.dumps({"texts": texts, "prefix": "passage"}).encode()
    req = urllib.request.Request(EMBED_URL, data=body, headers={
        "Content-Type": "application/json", "X-Embed-Token": TOKEN})
    return json.load(urllib.request.urlopen(req, timeout=180))


def build_chunks(posts):
    """Semantic chunks: one chunk per h3 section (~1600 chars cap), prefixed with
    post title + section heading so retrieval has rich signal."""
    chunks = []
    for p in posts:
        pid = p.get("id")
        title = p.get("title", "")
        cur, cur_len, heading = [], 0, None

        def flush():
            nonlocal cur, cur_len
            text = "\n".join(cur).strip()
            if len(text) >= 30:
                prefix = f"عنوان: {title}"
                if heading:
                    prefix += f"\nبخش: {heading}"
                chunks.append({"id": f"{pid}-{len(chunks)}", "post_id": pid,
                               "post_title": title, "text": prefix + "\n" + text})
            cur, cur_len = [], 0

        for b in p.get("content", []):
            t = b.get("type")
            fa = b.get("fa", "")
            if isinstance(fa, list):
                fa = "\n• " + "\n• ".join(str(x) for x in fa)
            fa = str(fa).strip()
            if not fa:
                continue
            if t == "h3":
                flush()
                heading = fa
                continue
            if t == "table" and isinstance(fa, dict):
                rows = [" | ".join(fa.get("headers", []))]
                rows += [" | ".join(str(x) for x in r) for r in fa.get("rows", [])]
                fa = "\n".join(rows)
            if cur_len + len(fa) > 1600 and cur:
                flush()
            cur.append(fa)
            cur_len += len(fa) + 1
        flush()
    return chunks


def main():
    posts = json.load(open(POSTS, encoding="utf-8"))
    posts = posts if isinstance(posts, list) else posts.get("posts", [])
    chunks = build_chunks(posts)
    print(f"📦 {len(posts)} posts -> {len(chunks)} chunks")

    vectors = []
    for i in range(0, len(chunks), 64):
        batch = [c["text"] for c in chunks[i:i + 64]]
        resp = embed_batch(batch)
        vectors.extend(resp["vectors"])
        print(f"  ✅ batch {i // 64 + 1}/{(len(chunks) + 63) // 64}: {len(resp['vectors'])} vectors (dim {resp['dims']})")
    dims = len(vectors[0])
    assert len(vectors) == len(chunks), f"mismatch {len(vectors)} vs {len(chunks)}"

    os.makedirs(f"{PROJ}/data", exist_ok=True)
    with open(f"{PROJ}/data/vectors.bin", "wb") as f:
        for v in vectors:
            f.write(struct.pack(f"<{dims}f", *v))
    meta = {"model": "openai/text-embedding-3-large", "dim": dims, "chunks": [
        {"id": c["id"], "post_id": c["post_id"],
         "post_title": c["post_title"], "text": c["text"]} for c in chunks]}
    with open(f"{PROJ}/data/chunks.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    bin_size = os.path.getsize(f"{PROJ}/data/vectors.bin") // 1024
    meta_size = os.path.getsize(f"{PROJ}/data/chunks.json") // 1024
    print(f"✅ vectors.bin ({bin_size} KB) + chunks.json ({meta_size} KB) | {len(chunks)} chunks, dim {dims}")


if __name__ == "__main__":
    main()
