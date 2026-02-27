"""
Clawd 🦞 — Memory System
Save, load, search, and manage box/CTF notes.
"""

import os
import glob
from datetime import datetime
import config


class Memory:
    """Persistent note storage for box and CTF documentation."""

    def __init__(self):
        os.makedirs(config.MEMORY_DIR, exist_ok=True)

    # ──────────────────────────────────────────
    # Save
    # ──────────────────────────────────────────

    def save(self, name: str, content: str, append: bool = False) -> str:
        """
        Save a note to memory.
        
        Args:
            name: Note name (e.g., 'htb-lame', 'ctf-nahamcon-web1')
            content: The content to save
            append: If True, append to existing note instead of overwriting
            
        Returns:
            The full path of the saved note.
        """
        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
        filepath = os.path.join(config.MEMORY_DIR, f"{safe_name}.md")

        mode = "a" if append and os.path.exists(filepath) else "w"
        with open(filepath, mode, encoding="utf-8") as f:
            if mode == "a":
                f.write(f"\n\n---\n*Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n")
            else:
                f.write(f"# {name}\n")
                f.write(f"*Created: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n")
            f.write(content)

        return filepath

    # ──────────────────────────────────────────
    # Load
    # ──────────────────────────────────────────

    def load(self, name: str) -> str | None:
        """
        Load a note from memory.
        
        Returns:
            The note content, or None if not found.
        """
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
        filepath = os.path.join(config.MEMORY_DIR, f"{safe_name}.md")

        if not os.path.exists(filepath):
            # Try fuzzy match
            matches = self.search(name)
            if len(matches) == 1:
                filepath = matches[0]["path"]
            else:
                return None

        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    # ──────────────────────────────────────────
    # List & Search
    # ──────────────────────────────────────────

    def list_notes(self) -> list[dict]:
        """List all saved notes with metadata."""
        notes = []
        for filepath in sorted(glob.glob(os.path.join(config.MEMORY_DIR, "*.md"))):
            stat = os.stat(filepath)
            notes.append({
                "name": os.path.splitext(os.path.basename(filepath))[0],
                "path": filepath,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        return notes

    def search(self, keyword: str) -> list[dict]:
        """Search notes by keyword in filename or content."""
        keyword_lower = keyword.lower()
        results = []

        for note in self.list_notes():
            # Check filename
            if keyword_lower in note["name"].lower():
                results.append(note)
                continue

            # Check content
            try:
                with open(note["path"], "r", encoding="utf-8") as f:
                    if keyword_lower in f.read().lower():
                        results.append(note)
            except Exception:
                pass

        return results

    # ──────────────────────────────────────────
    # Delete
    # ──────────────────────────────────────────

    def delete(self, name: str) -> bool:
        """Delete a note. Returns True if deleted."""
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
        filepath = os.path.join(config.MEMORY_DIR, f"{safe_name}.md")

        if os.path.exists(filepath):
            os.remove(filepath)
            return True
        return False
