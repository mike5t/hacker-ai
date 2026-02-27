"""
Clawd 🦞 — Notes Index
Lightweight tagged chunk retrieval system.
Stores key tool outputs as searchable tagged chunks to avoid context overflow.
No embeddings — just keyword/tag matching.
"""

import os
import json
import re
from datetime import datetime
import config

NOTES_DIR = os.path.join(config.MEMORY_DIR, "notes_index")
os.makedirs(NOTES_DIR, exist_ok=True)

# ──────────────────────────────────────────────
# Auto-tagging rules: command pattern → tags
# ──────────────────────────────────────────────

AUTO_TAG_RULES = [
    (r"\bnmap\b",                   ["nmap", "recon", "ports"]),
    (r"\bgobuster\b|\bffuf\b|\bferoxbuster\b|\bdirsearch\b", ["web", "dirs", "brute"]),
    (r"\bnikto\b",                  ["web", "vuln-scan"]),
    (r"\bsmbclient\b|\bsmbmap\b|\benum4linux\b", ["smb", "shares"]),
    (r"\bftp\b|\bcurl ftp\b",      ["ftp"]),
    (r"\bssh\b|\bsshpass\b",       ["ssh", "creds"]),
    (r"\bhydra\b|\bmedusa\b",      ["brute", "creds"]),
    (r"\bsqlmap\b",                ["web", "sqli"]),
    (r"\bcurl\b|\bwget\b",         ["web", "http"]),
    (r"\bwhatweb\b|\bwappalyzer\b",["web", "recon"]),
    (r"\bldapsearch\b",            ["ldap", "recon"]),
    (r"\bsnmpwalk\b",              ["snmp", "recon"]),
    (r"\blinpeas\b|\bwinpeas\b",   ["privesc", "enum"]),
    (r"\bsudo -l\b",               ["privesc"]),
    (r"\bcat /etc/passwd\b",       ["enum", "users"]),
    (r"\bhashcat\b|\bjohn\b",      ["creds", "cracking"]),
    (r"\bsearchsploit\b",          ["exploit", "research"]),
    (r"\bwhoami\b|\bid\b",         ["enum", "foothold"]),
    (r"\bnetstat\b|\bss -\b",      ["enum", "network"]),
    (r"\bfind / \b|\bfind /\b",    ["enum", "files"]),
]


def _index_path(target: str) -> str:
    """Get the notes index JSON path for a target."""
    safe = target.replace(".", "-").replace("/", "-").replace(":", "-")
    return os.path.join(NOTES_DIR, f"{safe}.json")


def _load_index(target: str) -> list[dict]:
    """Load all chunks for a target."""
    path = _index_path(target)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_index(target: str, chunks: list[dict]):
    """Save all chunks for a target."""
    path = _index_path(target)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2)


def auto_tag(command: str) -> list[str]:
    """Auto-detect tags from a command string."""
    tags = set()
    cmd_lower = command.lower()
    for pattern, tag_list in AUTO_TAG_RULES:
        if re.search(pattern, cmd_lower):
            tags.update(tag_list)
    return sorted(tags) if tags else ["misc"]


# ──────────────────────────────────────────────
# Store
# ──────────────────────────────────────────────

def store_chunk(target: str, content: str, tags: list[str],
                source: str = "", title: str = "") -> dict:
    """
    Store a tagged chunk of output/notes for a target.

    Args:
        target: IP or hostname
        content: The text to store (scan output, notes, etc.)
        tags: List of tags for retrieval, e.g. ["nmap", "recon"]
        source: What generated this (e.g. "nmap -sC -sV 10.129.5.190")
        title: Optional short title for the chunk

    Returns:
        Dict with chunk ID and confirmation.
    """
    chunks = _load_index(target)

    chunk_id = f"N{len(chunks) + 1}"

    # Truncate very long output to keep chunks manageable
    if len(content) > 3000:
        content = content[:1500] + f"\n\n... [TRUNCATED — {len(content)} chars total] ...\n\n" + content[-1500:]

    chunk = {
        "id": chunk_id,
        "title": title or f"{', '.join(tags)} output",
        "content": content,
        "tags": [t.lower() for t in tags],
        "source": source,
        "timestamp": datetime.now().isoformat(),
    }

    chunks.append(chunk)
    _save_index(target, chunks)

    return {
        "success": True,
        "message": f"Stored chunk {chunk_id} for {target} (tags: {tags})",
        "chunk_id": chunk_id,
        "total_chunks": len(chunks),
    }


# ──────────────────────────────────────────────
# Search / Retrieve
# ──────────────────────────────────────────────

def search_chunks(target: str, query: str = "",
                  tags: list[str] | None = None) -> dict:
    """
    Search stored chunks by keyword and/or tags.

    Args:
        target: IP or hostname
        query: Keyword to search in content/title/source (optional)
        tags: Filter by tags (optional, matches ANY tag)

    Returns:
        Dict with matching chunks (content included).
    """
    chunks = _load_index(target)
    if not chunks:
        return {
            "target": target,
            "matches": [],
            "message": "No notes stored for this target yet.",
        }

    results = []
    query_lower = query.lower().strip() if query else ""
    filter_tags = {t.lower() for t in tags} if tags else set()

    for chunk in chunks:
        # Tag filter: chunk must have at least one matching tag
        if filter_tags and not filter_tags.intersection(set(chunk["tags"])):
            continue

        # Keyword filter: must appear in content, title, or source
        if query_lower:
            searchable = (
                chunk["content"].lower() +
                chunk["title"].lower() +
                chunk.get("source", "").lower()
            )
            if query_lower not in searchable:
                continue

        results.append(chunk)

    # Build a compact summary for the LLM
    if results:
        summaries = []
        for r in results:
            summaries.append(
                f"### [{r['id']}] {r['title']} (tags: {', '.join(r['tags'])})\n"
                f"Source: `{r.get('source', 'manual')}`\n\n"
                f"{r['content']}"
            )
        combined = "\n\n---\n\n".join(summaries)
    else:
        combined = "No matching notes found."

    return {
        "target": target,
        "query": query,
        "tags_filter": list(filter_tags) if filter_tags else None,
        "match_count": len(results),
        "results": combined,
    }


def list_chunks(target: str) -> dict:
    """List all stored chunks for a target (titles + tags only, no content)."""
    chunks = _load_index(target)
    index = []
    for c in chunks:
        index.append({
            "id": c["id"],
            "title": c["title"],
            "tags": c["tags"],
            "source": c.get("source", ""),
            "size": len(c["content"]),
        })
    return {
        "target": target,
        "total_chunks": len(index),
        "index": index,
    }


# ──────────────────────────────────────────────
# Auto-capture helper
# ──────────────────────────────────────────────

# Minimum output length to auto-store (skip trivial outputs)
MIN_AUTO_CAPTURE_LEN = 100

def should_auto_capture(command: str, stdout: str, exit_code: int) -> bool:
    """Decide if a command's output is worth auto-storing."""
    if exit_code != 0:
        return False
    if len(stdout.strip()) < MIN_AUTO_CAPTURE_LEN:
        return False
    # Only auto-capture commands that match known recon/enum patterns
    tags = auto_tag(command)
    return tags != ["misc"]


def auto_capture(target: str, command: str, stdout: str) -> dict | None:
    """
    Auto-store significant command output as a tagged chunk.
    Called by the engine after successful commands.
    Returns the store result, or None if skipped.
    """
    tags = auto_tag(command)
    # Build a title from the command
    cmd_short = command[:60] + ("..." if len(command) > 60 else "")
    return store_chunk(
        target=target,
        content=stdout,
        tags=tags,
        source=command,
        title=cmd_short,
    )
