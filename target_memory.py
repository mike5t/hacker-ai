"""
Clawd 🦞 — Target Memory System
3-bucket structured memory: Facts, Failed Attempts, Hypotheses.
Prevents looping on failed attempts and separates evidence from guesses.
"""

import os
import json
from datetime import datetime
import config

TARGETS_DIR = os.path.join(config.MEMORY_DIR, "targets")
os.makedirs(TARGETS_DIR, exist_ok=True)


def _target_path(target: str) -> str:
    """Get the JSON file path for a target."""
    safe = target.replace(".", "-").replace("/", "-").replace(":", "-")
    return os.path.join(TARGETS_DIR, f"{safe}.json")


def _load(target: str) -> dict:
    """Load target memory from disk, or return empty structure."""
    path = _target_path(target)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "target": target,
        "created": datetime.now().isoformat(),
        "facts": [],
        "failed": [],
        "hypotheses": [],
    }


def _save(target: str, data: dict):
    """Save target memory to disk."""
    data["updated"] = datetime.now().isoformat()
    path = _target_path(target)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ──────────────────────────────────────────────
# Bucket A: Confirmed Facts
# ──────────────────────────────────────────────

def add_fact(target: str, fact: str, evidence: str = "") -> dict:
    """
    Record a confirmed fact about a target.

    Args:
        target: IP or hostname (e.g. "10.129.5.190")
        fact: The confirmed fact (e.g. "22/tcp open ssh OpenSSH 9.2p1")
        evidence: How this was confirmed (e.g. "nmap -sC -sV output")

    Returns:
        Dict with success status and current fact count.
    """
    data = _load(target)

    # Deduplicate — don't add the same fact twice
    for existing in data["facts"]:
        if existing["fact"].strip().lower() == fact.strip().lower():
            return {
                "success": True,
                "message": "Fact already recorded",
                "fact_count": len(data["facts"]),
            }

    data["facts"].append({
        "fact": fact,
        "evidence": evidence,
        "timestamp": datetime.now().isoformat(),
    })
    _save(target, data)

    return {
        "success": True,
        "message": f"Fact recorded for {target}",
        "fact_count": len(data["facts"]),
    }


# ──────────────────────────────────────────────
# Bucket B: Failed Attempts
# ──────────────────────────────────────────────

def add_failed(target: str, attempt: str, result: str,
               exit_code: int | str | None = None) -> dict:
    """
    Record a failed attempt so it's never retried.

    Args:
        target: IP or hostname
        attempt: What was tried (e.g. "ssh user:password")
        result: What happened (e.g. "Permission denied")
        exit_code: Exit code if applicable

    Returns:
        Dict with success status and current failed count.
    """
    data = _load(target)

    # Deduplicate
    for existing in data["failed"]:
        if existing["attempt"].strip().lower() == attempt.strip().lower():
            return {
                "success": True,
                "message": "Attempt already recorded as failed",
                "failed_count": len(data["failed"]),
            }

    data["failed"].append({
        "attempt": attempt,
        "result": result,
        "exit_code": str(exit_code) if exit_code is not None else None,
        "timestamp": datetime.now().isoformat(),
    })
    _save(target, data)

    return {
        "success": True,
        "message": f"Failed attempt recorded for {target}",
        "failed_count": len(data["failed"]),
    }


# ──────────────────────────────────────────────
# Bucket C: Hypotheses
# ──────────────────────────────────────────────

def add_hypothesis(target: str, hypothesis: str) -> dict:
    """
    Add an unverified hypothesis about a target.

    Args:
        target: IP or hostname
        hypothesis: The theory (e.g. "Might have default SSH key")

    Returns:
        Dict with hypothesis ID and current count.
    """
    data = _load(target)

    # Deduplicate
    for existing in data["hypotheses"]:
        if existing["text"].strip().lower() == hypothesis.strip().lower():
            return {
                "success": True,
                "message": f"Hypothesis already exists (status: {existing['status']})",
                "id": existing["id"],
            }

    h_id = f"H{len(data['hypotheses']) + 1}"
    data["hypotheses"].append({
        "id": h_id,
        "text": hypothesis,
        "status": "untested",
        "timestamp": datetime.now().isoformat(),
    })
    _save(target, data)

    return {
        "success": True,
        "message": f"Hypothesis {h_id} added for {target}",
        "id": h_id,
        "status": "untested",
    }


def update_hypothesis(target: str, hypothesis_id: str, new_status: str,
                      evidence: str = "") -> dict:
    """
    Update a hypothesis status. Auto-promotes/demotes on confirm/disprove.

    Args:
        target: IP or hostname
        hypothesis_id: The ID (e.g. "H1")
        new_status: One of: testing, confirmed, disproved
        evidence: Supporting evidence for the status change

    Returns:
        Dict with updated status and any promotions/demotions.
    """
    valid_statuses = {"untested", "testing", "confirmed", "disproved"}
    if new_status not in valid_statuses:
        return {"success": False, "error": f"Invalid status. Use: {valid_statuses}"}

    data = _load(target)

    # Find the hypothesis
    found = None
    for h in data["hypotheses"]:
        if h["id"].lower() == hypothesis_id.lower():
            found = h
            break

    if not found:
        return {"success": False, "error": f"Hypothesis {hypothesis_id} not found"}

    old_status = found["status"]
    found["status"] = new_status
    found["status_updated"] = datetime.now().isoformat()

    result = {
        "success": True,
        "hypothesis": found["text"],
        "old_status": old_status,
        "new_status": new_status,
        "promoted": None,
    }

    # Auto-promote to facts on confirm
    if new_status == "confirmed":
        add_fact(target, found["text"], evidence or "Confirmed from hypothesis")
        data["hypotheses"].remove(found)
        result["promoted"] = "facts"
        _save(target, data)
        return result

    # Auto-demote to failed on disprove
    if new_status == "disproved":
        add_failed(target, found["text"], evidence or "Disproved hypothesis")
        data["hypotheses"].remove(found)
        result["promoted"] = "failed"
        _save(target, data)
        return result

    _save(target, data)
    return result


# ──────────────────────────────────────────────
# Recall / Summary
# ──────────────────────────────────────────────

def get_summary(target: str) -> str:
    """
    Get a formatted markdown summary of all 3 buckets for a target.
    Used for LLM context injection and /target command display.
    """
    data = _load(target)

    lines = [f"# 🎯 Target Intel: {target}\n"]

    # Facts
    lines.append("## ✅ Confirmed Facts")
    if data["facts"]:
        for f in data["facts"]:
            evidence_note = f" (via: {f['evidence']})" if f.get("evidence") else ""
            lines.append(f"- {f['fact']}{evidence_note}")
    else:
        lines.append("- (none yet)")

    lines.append("")

    # Failed
    lines.append("## ❌ Failed Attempts (DO NOT RETRY)")
    if data["failed"]:
        for f in data["failed"]:
            code = f" → exit {f['exit_code']}" if f.get("exit_code") else ""
            lines.append(f"- {f['attempt']}{code}: {f['result']}")
    else:
        lines.append("- (none yet)")

    lines.append("")

    # Hypotheses
    lines.append("## 💡 Hypotheses")
    if data["hypotheses"]:
        status_emoji = {
            "untested": "⬜",
            "testing": "🔄",
            "confirmed": "✅",
            "disproved": "❌",
        }
        for h in data["hypotheses"]:
            emoji = status_emoji.get(h["status"], "❓")
            lines.append(f"- [{h['id']}] {emoji} {h['text']} (status: {h['status']})")
    else:
        lines.append("- (none yet)")

    return "\n".join(lines)


def recall_target(target: str) -> dict:
    """
    Load all known intel for a target.

    Args:
        target: IP or hostname

    Returns:
        Dict with the formatted summary and raw counts.
    """
    data = _load(target)
    summary = get_summary(target)

    return {
        "target": target,
        "summary": summary,
        "counts": {
            "facts": len(data["facts"]),
            "failed": len(data["failed"]),
            "hypotheses": len(data["hypotheses"]),
        },
    }


def list_targets() -> list[str]:
    """List all tracked targets."""
    targets = []
    for filename in os.listdir(TARGETS_DIR):
        if filename.endswith(".json"):
            name = filename[:-5].replace("-", ".")
            # Fix the IP format (first 3 dots are periods, rest stay)
            targets.append(name)
    return targets
