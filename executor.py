"""
Clawd 🦞 — Command Executor
Runs shell commands, writes/reads files in the workspace.
"""

import os
import subprocess
import config
import target_memory
import notes_index


# Ensure workspace exists
os.makedirs(config.WORKSPACE_DIR, exist_ok=True)


def run_command(command: str, timeout: int | str | None = None) -> dict:
    """
    Execute a shell command inside WSL (Ubuntu) and capture output.
    
    Args:
        command: The shell command to run (Linux commands).
        timeout: Timeout in seconds (default from config, max 600s).
        
    Returns:
        Dict with: command, exit_code, stdout, stderr, timed_out
    """
    if timeout is None:
        timeout = config.CMD_TIMEOUT
    try:
        timeout = int(timeout)
    except (ValueError, TypeError):
        timeout = config.CMD_TIMEOUT
    timeout = min(timeout, config.CMD_TIMEOUT_MAX)

    # Convert Windows workspace path to WSL path
    wsl_workspace = _windows_to_wsl_path(config.WORKSPACE_DIR)

    # Build the bash script to run inside WSL
    # Use single quotes around paths with spaces
    bash_script = f"mkdir -p '{wsl_workspace}' && cd '{wsl_workspace}' && {command}"

    # Use list form to avoid Windows shell quoting issues
    # Run as root so nmap -O, raw sockets, etc. work
    wsl_args = ["wsl", "-u", "root", "bash", "-c", bash_script]

    try:
        result = subprocess.run(
            wsl_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        stdout = _truncate_output(result.stdout)
        stderr = _truncate_output(result.stderr)

        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
        }

    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "timed_out": True,
        }
    except Exception as e:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "timed_out": False,
        }


def write_file(path: str, content: str) -> dict:
    """
    Write content to a file in the workspace.
    
    Args:
        path: Filename or relative path (will be placed in workspace/).
        content: File content to write.
        
    Returns:
        Dict with: path, size, success, error
    """
    # Resolve path within workspace
    full_path = _resolve_path(path)

    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        return {
            "path": full_path,
            "size": len(content),
            "success": True,
            "error": None,
        }
    except Exception as e:
        return {
            "path": full_path,
            "size": 0,
            "success": False,
            "error": str(e),
        }


def read_file(path: str) -> dict:
    """
    Read a file from the workspace.
    
    Args:
        path: Filename or relative path within workspace/.
        
    Returns:
        Dict with: path, content, success, error
    """
    full_path = _resolve_path(path)

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        content = _truncate_output(content)

        return {
            "path": full_path,
            "content": content,
            "success": True,
            "error": None,
        }
    except Exception as e:
        return {
            "path": full_path,
            "content": "",
            "success": False,
            "error": str(e),
        }


def _resolve_path(path: str) -> str:
    """Resolve a path relative to the workspace directory."""
    if os.path.isabs(path) and path.startswith(config.WORKSPACE_DIR):
        return path
    return os.path.join(config.WORKSPACE_DIR, path)


def _windows_to_wsl_path(win_path: str) -> str:
    """Convert a Windows path to a WSL-compatible path.
    
    Example: C:\\Users\\Mike\\Desktop\\hacker ai\\workspace
          -> /mnt/c/Users/Mike/Desktop/hacker ai/workspace
    """
    # Normalize path
    path = win_path.replace("\\", "/")
    # Convert drive letter: C:/... -> /mnt/c/...
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        path = f"/mnt/{drive}{path[2:]}"
    return path


def _truncate_output(text: str, max_lines: int = 200, max_chars: int = 8000) -> str:
    """Truncate long output to keep context window manageable."""
    if not text:
        return text
        
    # Char limit
    if len(text) > max_chars:
        half = max_chars // 2
        text = (
            text[:half]
            + f"\n\n... [TRUNCATED — {len(text)} total chars] ...\n\n"
            + text[-half:]
        )

    # Line limit
    lines = text.split("\n")
    if len(lines) > max_lines:
        keep = max_lines // 2
        text = (
            "\n".join(lines[:keep])
            + f"\n\n... [TRUNCATED — {len(lines)} total lines] ...\n\n"
            + "\n".join(lines[-keep:])
        )

    return text


# ──────────────────────────────────────────────
# Target Memory Tools
# ──────────────────────────────────────────────

def log_fact(target: str, fact: str, evidence: str = "") -> dict:
    """Record a confirmed fact about a target."""
    return target_memory.add_fact(target, fact, evidence)


def log_failed(target: str, attempt: str, result: str,
               exit_code: int | str | None = None) -> dict:
    """Record a failed attempt so it's never retried."""
    return target_memory.add_failed(target, attempt, result, exit_code)


def log_hypothesis(target: str, hypothesis: str = "",
                   status: str = "", hypothesis_id: str = "") -> dict:
    """Add or update a hypothesis about a target."""
    if hypothesis_id and status:
        return target_memory.update_hypothesis(target, hypothesis_id, status)
    if not hypothesis:
        return {"error": "Must provide either 'hypothesis' (to add) or 'hypothesis_id' and 'status' (to update)."}
    return target_memory.add_hypothesis(target, hypothesis)


def recall_target(target: str) -> dict:
    """Load all known intel for a target."""
    return target_memory.recall_target(target)


# ──────────────────────────────────────────────
# Notes Index Tools
# ──────────────────────────────────────────────

def store_note(target: str, content: str, tags: list[str] | str = "",
               title: str = "", source: str = "") -> dict:
    """Store a tagged note/chunk for a target."""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()] or ["misc"]
    return notes_index.store_chunk(target, content, tags, source, title)


def search_notes(target: str, query: str = "",
                 tags: str | list[str] = "") -> dict:
    """Search stored notes by keyword and/or tags."""
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
    else:
        tag_list = tags or None
    return notes_index.search_chunks(target, query, tag_list)


# ──────────────────────────────────────────────
# Web Browsing Tools
# ──────────────────────────────────────────────

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
import ssl

class CTFHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_blocks = []
        self.links = set()
        self.comments = []
        self._ignore_tags = {'script', 'style', 'head', 'meta', 'link'}
        self._current_tag = ""

    def handle_starttag(self, tag, attrs):
        self._current_tag = tag
        if tag in ('a', 'link', 'script', 'img', 'form'):
            for name, value in attrs:
                if name in ('href', 'src', 'action') and value and not value.startswith('data:'):
                    self.links.add(value)

    def handle_endtag(self, tag):
        self._current_tag = ""

    def handle_data(self, data):
        if self._current_tag not in self._ignore_tags:
            text = data.strip()
            if text:
                self.text_blocks.append(text)

    def handle_comment(self, data):
        comment = data.strip()
        if comment:
            self.comments.append(comment)


def read_webpage(url: str) -> dict:
    """
    Fetch a webpage (from inside WSL) and extract clean text, links, and comments.
    """
    if not url.startswith("http"):
        url = "http://" + url

    # Fetch via WSL so it uses the VPN connection
    cmd = f"curl -sL -k -w '\\n%{{http_code}}' -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Clawd/1.0' '{url}'"
    res = run_command(cmd, timeout=15)
    
    if res["exit_code"] != 0:
        return {
            "error": f"Failed to connect to {url}", 
            "url": url, 
            "curl_stderr": res["stderr"]
        }

    # Extract the HTTP status code appended by curl's -w flag
    stdout = res["stdout"].strip()
    if "\n" in stdout:
        html, status_str = stdout.rsplit("\n", 1)
        try:
            status = int(status_str.strip())
        except ValueError:
            status = 0
            html = stdout
    else:
        status = 0
        html = stdout

    parser = CTFHtmlParser()
    parser.feed(html)

    # Compile the results for the LLM
    result = {
        "url": url,
        "status_code": status,
        "links_found": sorted(list(parser.links)),
        "hidden_comments": parser.comments,
        "visible_text": "\n".join(parser.text_blocks),
    }

    # Truncate text if it's monstrously huge (protect LLM context)
    max_text = 4000
    if len(result["visible_text"]) > max_text:
        result["visible_text"] = result["visible_text"][:max_text] + f"\n\n... [TRUNCATED — {len(result['visible_text'])} chars total]"

    return result
