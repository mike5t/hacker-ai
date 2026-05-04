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
    import platform
    if platform.system() == "Linux":
        wsl_args = ["sudo", "-n", "bash", "-c", bash_script]
    else:
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
                   status: str = "", hypothesis_id: str = "",
                   **kwargs) -> dict:
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
# IDOR Enumeration Tool
# ──────────────────────────────────────────────

def idor_enum(url_template: str, start: int | str = 1, end: int | str = 20,
              match_codes: str = "200,301,302") -> dict:
    """
    Enumerate IDOR (Insecure Direct Object Reference) by fuzzing a numeric
    parameter in a URL.  Uses Playwright so JS-rendered pages are handled.

    Args:
        url_template: URL with {FUZZ} placeholder, e.g. "http://target/profile?id={FUZZ}"
        start: First number to try (inclusive, default 1)
        end: Last number to try (inclusive, default 20, max 100)
        match_codes: Comma-separated HTTP status codes to keep (default "200,301,302")

    Returns:
        Dict with: url_template, range, hits[], total_hits, errors
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    try:
        start = max(0, int(start))
    except (ValueError, TypeError):
        start = 1
    try:
        end = min(int(end), start + 100)
    except (ValueError, TypeError):
        end = min(start + 20, start + 100)

    try:
        codes = {int(c.strip()) for c in str(match_codes).split(",")}
    except ValueError:
        codes = {200, 301, 302}

    if "{FUZZ}" not in url_template and "{fuzz}" not in url_template.lower():
        return {"error": "url_template must contain {FUZZ} placeholder, e.g. http://target/page?id={FUZZ}"}

    hits = []
    errors = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Clawd/2.0"
                ),
                ignore_https_errors=True,
            )
            page = context.new_page()

            for i in range(start, end + 1):
                url = url_template.replace("{FUZZ}", str(i)).replace("{fuzz}", str(i))
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=10000)
                    if resp is None:
                        continue

                    status = resp.status
                    if status not in codes:
                        continue

                    # Extract page title and visible text snippet
                    try:
                        title = page.title() or ""
                    except Exception:
                        title = ""
                    try:
                        body_text = page.inner_text("body")[:300]
                    except Exception:
                        body_text = ""

                    # Skip obvious error pages
                    lower_text = body_text.lower()
                    if any(err in lower_text for err in ["not found", "404", "no user",
                                                          "does not exist", "invalid id",
                                                          "access denied"]):
                        continue

                    hits.append({
                        "fuzz_value": i,
                        "url": url,
                        "status": status,
                        "title": title,
                        "body_snippet": body_text[:200],
                    })

                except PwTimeout:
                    errors.append(f"Timeout on {url}")
                except Exception as e:
                    errors.append(f"Error on {url}: {str(e)[:100]}")

                import time
                time.sleep(0.2)  # rate limit

            browser.close()

    except Exception as e:
        return {
            "error": f"IDOR enumeration failed: {str(e)[:300]}",
            "url_template": url_template,
        }

    return {
        "url_template": url_template,
        "range": f"{start}-{end}",
        "total_hits": len(hits),
        "hits": hits[:30],  # cap to avoid huge output
        "errors": errors[:5] if errors else [],
    }


# ──────────────────────────────────────────────
# File Download Tool
# ──────────────────────────────────────────────

LOOT_DIR = os.path.join(config.WORKSPACE_DIR, "loot")
os.makedirs(LOOT_DIR, exist_ok=True)

def download_file(url: str, filename: str = "") -> dict:
    """
    Download a file from a URL and save it to workspace/loot/.
    Uses curl via WSL so it goes through the VPN.

    Args:
        url: Full URL to download, e.g. "http://10.10.10.5/files/backup.pcap"
        filename: Optional filename to save as (default: derived from URL)

    Returns:
        Dict with: url, saved_path, size_bytes, success, error
    """
    if not url.startswith("http"):
        url = "http://" + url

    # Derive filename from URL if not specified
    if not filename:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        filename = os.path.basename(unquote(parsed.path)) or "downloaded_file"

    # Sanitise filename
    filename = "".join(c for c in filename if c.isalnum() or c in "._-").strip()
    if not filename:
        filename = "downloaded_file"

    save_path = os.path.join(LOOT_DIR, filename)
    wsl_save_path = _windows_to_wsl_path(save_path)

    # Ensure loot dir exists in WSL
    wsl_loot = _windows_to_wsl_path(LOOT_DIR)

    # Download via curl in WSL
    cmd = (
        f"mkdir -p '{wsl_loot}' && "
        f"curl -sL -k -o '{wsl_save_path}' -w '%{{http_code}}' "
        f"-A 'Mozilla/5.0 Clawd/2.0' '{url}'"
    )
    result = run_command(cmd, timeout=60)

    if result.get("timed_out"):
        return {"url": url, "saved_path": "", "size_bytes": 0,
                "success": False, "error": "Download timed out after 60s"}

    if result.get("exit_code", -1) != 0:
        return {"url": url, "saved_path": "", "size_bytes": 0,
                "success": False,
                "error": f"curl failed: {result.get('stderr', '')[:200]}"}

    # Check HTTP status
    http_code = result.get("stdout", "").strip()
    try:
        code = int(http_code)
    except ValueError:
        code = 0

    if code == 404:
        return {"url": url, "saved_path": "", "size_bytes": 0,
                "success": False, "error": "HTTP 404 — file not found"}
    if code >= 400:
        return {"url": url, "saved_path": "", "size_bytes": 0,
                "success": False, "error": f"HTTP {code} error"}

    # Get file size
    try:
        size = os.path.getsize(save_path)
    except OSError:
        size = 0

    return {
        "url": url,
        "saved_path": save_path,
        "filename": filename,
        "size_bytes": size,
        "http_status": code,
        "success": True,
        "error": None,
        "hint": "Use analyze_pcap to extract creds from .pcap files, or read_file to view text files.",
    }


# ──────────────────────────────────────────────
# PCAP / Network Capture Analysis Tool
# ──────────────────────────────────────────────

def analyze_pcap(filepath: str, display_filter: str = "") -> dict:
    """
    Analyze a .pcap or .pcapng file using tshark (Wireshark CLI).
    Extracts credentials, interesting protocols, and connection summaries.

    Args:
        filepath: Path to the pcap file (relative to workspace or absolute).
        display_filter: Optional tshark display filter (e.g. "http.authbasic" or "ftp").
                       If empty, uses a default filter that looks for credentials.

    Returns:
        Dict with: filepath, protocol_summary, credentials[], raw_output, success, error
    """
    full_path = _resolve_path(filepath)

    if not os.path.isfile(full_path):
        return {"error": f"File not found: {full_path}", "filepath": filepath,
                "success": False}

    wsl_path = _windows_to_wsl_path(full_path)

    results = {
        "filepath": filepath,
        "credentials": [],
        "protocol_summary": "",
        "connections": [],
        "raw_output": "",
        "success": False,
        "error": None,
    }

    # 1. Protocol hierarchy — what protocols are in the capture
    proto_cmd = f"tshark -r '{wsl_path}' -q -z io,phs 2>/dev/null | head -40"
    proto_result = run_command(proto_cmd, timeout=30)
    if proto_result.get("exit_code") == 0:
        results["protocol_summary"] = proto_result.get("stdout", "")

    # 2. Extract credentials — FTP, Telnet, HTTP Basic Auth, HTTP forms
    if display_filter:
        cred_filter = display_filter
    else:
        cred_filter = (
            "ftp.request.command == USER || ftp.request.command == PASS || "
            "http.authbasic || "
            "http.request.method == POST || "
            "telnet.data"
        )

    cred_cmd = (
        f"tshark -r '{wsl_path}' -Y '{cred_filter}' "
        f"-T fields -e frame.number -e ip.src -e ip.dst -e tcp.dstport "
        f"-e ftp.request.command -e ftp.request.arg "
        f"-e http.authbasic -e http.request.uri -e http.file_data "
        f"-e telnet.data "
        f"-E header=y -E separator='|' 2>/dev/null | head -60"
    )
    cred_result = run_command(cred_cmd, timeout=30)

    if cred_result.get("exit_code") == 0:
        raw = cred_result.get("stdout", "")
        results["raw_output"] = raw

        # Parse tshark tabular output into structured creds
        lines = raw.strip().split("\n")
        if len(lines) > 1:
            headers = lines[0].split("|")
            for line in lines[1:]:
                fields = line.split("|")
                if len(fields) >= 6:
                    entry = {}
                    for h, v in zip(headers, fields):
                        h = h.strip()
                        v = v.strip()
                        if v:
                            entry[h] = v
                    if entry:
                        results["credentials"].append(entry)

    # 3. TCP conversations summary
    conv_cmd = f"tshark -r '{wsl_path}' -q -z conv,tcp 2>/dev/null | head -25"
    conv_result = run_command(conv_cmd, timeout=30)
    if conv_result.get("exit_code") == 0:
        results["connections"] = conv_result.get("stdout", "")

    results["success"] = True
    cred_count = len(results["credentials"])

    if cred_count > 0:
        results["hint"] = (
            f"Found {cred_count} credential-related packet(s). "
            "Try using extracted usernames/passwords with SSH, FTP, or web login."
        )
    else:
        results["hint"] = (
            "No credentials found with default filters. "
            "Try a custom display_filter like 'http' or 'tcp.port==21' "
            "or look at the protocol_summary for clues."
        )

    return results


# ──────────────────────────────────────────────
# Web Recon Tools
# ──────────────────────────────────────────────

def web_recon(url: str, max_pages: int | str = 30,
              headless: bool | str = True) -> dict:
    """Run browser-driven web recon against a target URL."""
    import web_recon as wr

    try:
        max_pages = int(max_pages)
    except (ValueError, TypeError):
        max_pages = 30

    if isinstance(headless, str):
        headless = headless.lower() != "false"

    try:
        full_report = wr.run_web_recon(url, max_pages=max_pages, headless=headless)
    except Exception as e:
        return {"error": f"Web recon failed: {str(e)[:300]}", "url": url}

    # Auto-store as a tagged note
    from urllib.parse import urlparse
    parsed = urlparse(url if url.startswith("http") else f"http://{url}")
    target = parsed.hostname or url

    summary_text = full_report.get("summary", "")
    notes_index.store_chunk(
        target=target,
        content=summary_text,
        tags=["web", "recon", "vuln", "browser"],
        source=f"web_recon({url})",
        title=f"Browser recon: {url}",
    )

    # Auto-log findings as hypotheses in target memory
    for finding in full_report.get("findings", []):
        if finding["severity"] in ("critical", "high", "medium"):
            target_memory.add_hypothesis(target, finding["title"])

    # Return ONLY compact report to LLM (not full page evidence)
    return wr.compact_report(full_report)


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
