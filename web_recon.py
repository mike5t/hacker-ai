"""
Clawd 🦞 — Browser-Driven Web Recon & Vulnerability Discovery
Uses Playwright (Chromium) to crawl a target web app, collect structured
evidence, and generate prioritised vulnerability hypotheses.

All heavy lifting happens in Python — only a compact summary is returned
to the LLM so the 8B model is never overloaded.

Usage (standalone):
    python web_recon.py http://10.10.10.5
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

import config

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

SCREENSHOT_DIR = os.path.join(config.WORKSPACE_DIR, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Clawd/2.0"
)

COMMON_PATHS = [
    "/robots.txt", "/sitemap.xml", "/admin", "/login",
    "/api", "/uploads", "/backup", "/.git/", "/.env",
    "/wp-admin", "/phpmyadmin", "/console", "/debug",
    "/server-status", "/server-info", "/.htaccess",
    "/wp-login.php", "/administrator", "/.DS_Store",
]

SECURITY_HEADERS = [
    "content-security-policy", "x-frame-options",
    "x-content-type-options", "strict-transport-security",
    "x-xss-protection", "referrer-policy",
    "permissions-policy", "cross-origin-opener-policy",
    "cross-origin-resource-policy",
]

# Regex patterns to extract API-like endpoints from JS bundles
JS_ENDPOINT_PATTERNS = [
    re.compile(r'["\'](/api/[a-zA-Z0-9_/\-]+)["\']'),
    re.compile(r'["\'](/v[0-9]+/[a-zA-Z0-9_/\-]+)["\']'),
    re.compile(r'["\'](/graphql)["\']'),
    re.compile(r'["\'](/rest/[a-zA-Z0-9_/\-]+)["\']'),
    re.compile(r'fetch\s*\(\s*["\']([^"\']+)["\']'),
    re.compile(r'axios\.[a-z]+\s*\(\s*["\']([^"\']+)["\']'),
]

REQUEST_DELAY = 0.3  # seconds between page navigations (rate limit)


# ──────────────────────────────────────────────
# Core Recon Function
# ──────────────────────────────────────────────

def run_web_recon(
    base_url: str,
    max_pages: int = 30,
    headless: bool = True,
    take_screenshots: bool = True,
) -> dict:
    """
    Crawl a target web app and produce a structured vulnerability report.

    Args:
        base_url: Target URL, e.g. "http://10.10.10.5" or "http://10.10.10.5:8080"
        max_pages: Max pages to crawl (default 30)
        headless: Run browser invisibly (default True)
        take_screenshots: Save page screenshots (default True)

    Returns:
        Dict with: target, pages[], forms[], endpoints[], security_headers,
                   findings[], summary (human-readable)
    """
    # Normalise URL
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    base_url = base_url.rstrip("/")

    parsed_base = urlparse(base_url)
    scope_host = parsed_base.hostname
    scope_port = parsed_base.port or (443 if parsed_base.scheme == "https" else 80)

    report = {
        "target": base_url,
        "scope_host": scope_host,
        "scope_port": scope_port,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pages_crawled": 0,
        "pages": [],
        "forms": [],
        "endpoints": set(),
        "security_headers": {},
        "findings": [],
        "summary": "",
    }

    visited = set()
    queue = ["/"]  # BFS queue of paths
    network_log = []

    # Add common paths to the queue
    for p in COMMON_PATHS:
        if p not in queue:
            queue.append(p)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 900},
        )

        # Network interception for logging
        page = context.new_page()

        def _on_response(response):
            try:
                network_log.append({
                    "url": response.url,
                    "status": response.status,
                    "method": response.request.method,
                    "content_type": response.headers.get("content-type", ""),
                })
            except Exception:
                pass

        page.on("response", _on_response)

        while queue and report["pages_crawled"] < max_pages:
            path = queue.pop(0)
            full_url = base_url + path if path.startswith("/") else path

            # Scope check
            parsed = urlparse(full_url)
            if parsed.hostname != scope_host:
                continue
            # Deduplicate
            norm_path = parsed.path.rstrip("/") or "/"
            if norm_path in visited:
                continue
            visited.add(norm_path)

            # Visit the page
            page_data = _visit_page(
                page, full_url, base_url, scope_host,
                take_screenshots, report["pages_crawled"],
            )
            if page_data is None:
                continue

            report["pages"].append(page_data)
            report["pages_crawled"] += 1

            # Collect security headers from the first successful page
            if not report["security_headers"] and page_data.get("status") == 200:
                report["security_headers"] = page_data.get("security_headers", {})

            # Enqueue discovered in-scope links
            for link in page_data.get("links", []):
                link_parsed = urlparse(link)
                link_path = link_parsed.path.rstrip("/") or "/"
                if link_parsed.hostname == scope_host and link_path not in visited:
                    queue.append(link_path)

            # Collect forms
            for form in page_data.get("forms", []):
                form["found_on"] = page_data["url"]
                report["forms"].append(form)

            # Collect endpoints
            for ep in page_data.get("js_endpoints", []):
                report["endpoints"].add(ep)

            time.sleep(REQUEST_DELAY)

        # ── JS Bundle Endpoint Extraction ──
        _extract_js_endpoints(page, base_url, report)

        browser.close()

    # Convert set to list for JSON serialisation
    report["endpoints"] = sorted(list(report["endpoints"]))

    # ── Vulnerability Hypothesis Engine ──
    report["findings"] = _generate_findings(report)

    # ── Human-Readable Summary ──
    report["summary"] = _build_summary(report)

    return report


# ──────────────────────────────────────────────
# Page Visitor
# ──────────────────────────────────────────────

def _visit_page(page, url, base_url, scope_host, take_screenshots, page_idx):
    """Visit a single page and extract all evidence."""
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except PwTimeout:
        return {"url": url, "status": -1, "error": "timeout", "title": ""}
    except Exception as e:
        return {"url": url, "status": -1, "error": str(e)[:200], "title": ""}

    if resp is None:
        return None

    status = resp.status
    final_url = page.url

    # Redirect chain
    redirects = []
    req = resp.request
    while req.redirected_from:
        redirects.insert(0, req.redirected_from.url)
        req = req.redirected_from

    # Page title
    try:
        title = page.title() or ""
    except Exception:
        title = ""

    # Visible text (truncated)
    try:
        visible_text = page.inner_text("body")[:500]
    except Exception:
        visible_text = ""

    # Links (in-scope only)
    links = set()
    try:
        for el in page.query_selector_all("a[href]"):
            href = el.get_attribute("href")
            if href:
                abs_url = urljoin(final_url, href)
                if urlparse(abs_url).hostname == scope_host:
                    links.add(abs_url)
    except Exception:
        pass

    # Forms
    forms = []
    try:
        for form_el in page.query_selector_all("form"):
            action = form_el.get_attribute("action") or ""
            method = (form_el.get_attribute("method") or "GET").upper()
            inputs = []
            for inp in form_el.query_selector_all("input, select, textarea"):
                inp_name = inp.get_attribute("name") or ""
                inp_type = inp.get_attribute("type") or "text"
                inputs.append({"name": inp_name, "type": inp_type})
            forms.append({
                "action": urljoin(final_url, action) if action else final_url,
                "method": method,
                "inputs": inputs,
            })
    except Exception:
        pass

    # Scripts
    script_srcs = []
    inline_script_count = 0
    try:
        for s in page.query_selector_all("script"):
            src = s.get_attribute("src")
            if src:
                script_srcs.append(urljoin(final_url, src))
            else:
                inline_script_count += 1
    except Exception:
        pass

    # Cookies
    cookies = []
    try:
        for c in page.context.cookies():
            cookies.append({
                "name": c["name"],
                "domain": c.get("domain", ""),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "None"),
            })
    except Exception:
        pass

    # Security headers
    sec_headers = {}
    try:
        all_headers = resp.all_headers()
        for h in SECURITY_HEADERS:
            val = all_headers.get(h)
            if val:
                sec_headers[h] = val
    except Exception:
        pass

    # Screenshot
    screenshot_path = None
    if take_screenshots:
        try:
            fname = f"page_{page_idx:03d}.png"
            screenshot_path = os.path.join(SCREENSHOT_DIR, fname)
            page.screenshot(path=screenshot_path, full_page=False)
        except Exception:
            screenshot_path = None

    # JS endpoint hints from inline scripts
    js_endpoints = set()
    try:
        html = page.content()
        for pattern in JS_ENDPOINT_PATTERNS:
            for match in pattern.findall(html):
                if match.startswith("/"):
                    js_endpoints.add(match)
    except Exception:
        pass

    # Downloadable / loot-worthy files
    LOOT_EXTENSIONS = (
        '.pcap', '.pcapng', '.zip', '.tar', '.tar.gz', '.tgz', '.7z',
        '.bak', '.old', '.sql', '.db', '.sqlite', '.log', '.conf',
        '.cfg', '.csv', '.key', '.pem', '.p12', '.pfx', '.kdbx',
        '.ovpn', '.cap', '.dmp',
    )
    downloadable_files = []
    try:
        for el in page.query_selector_all("a[href]"):
            href = el.get_attribute("href") or ""
            href_lower = href.lower()
            if any(href_lower.endswith(ext) for ext in LOOT_EXTENSIONS):
                abs_href = urljoin(final_url, href)
                downloadable_files.append(abs_href)
    except Exception:
        pass

    return {
        "url": final_url,
        "status": status,
        "redirects": redirects,
        "title": title,
        "visible_text": visible_text,
        "links": sorted(list(links)),
        "forms": forms,
        "scripts": script_srcs,
        "inline_scripts": inline_script_count,
        "cookies": cookies,
        "security_headers": sec_headers,
        "screenshot": screenshot_path,
        "js_endpoints": sorted(list(js_endpoints)),
        "downloadable_files": downloadable_files,
    }


# ──────────────────────────────────────────────
# JS Bundle Endpoint Extraction
# ──────────────────────────────────────────────

def _extract_js_endpoints(page, base_url, report):
    """Fetch external JS bundles and scan for API endpoint patterns."""
    js_urls = set()
    for pg in report["pages"]:
        for src in pg.get("scripts", []):
            js_urls.add(src)

    for js_url in list(js_urls)[:10]:  # cap at 10 bundles
        try:
            resp = page.goto(js_url, timeout=8000)
            if resp and resp.status == 200:
                body = resp.text()
                for pattern in JS_ENDPOINT_PATTERNS:
                    for match in pattern.findall(body):
                        if match.startswith("/"):
                            report["endpoints"].add(match)
        except Exception:
            pass


# ──────────────────────────────────────────────
# Vulnerability Hypothesis Engine
# ──────────────────────────────────────────────

def _generate_findings(report: dict) -> list[dict]:
    """Rule-based vulnerability hypothesis generator."""
    findings = []
    fid = 0

    def _add(title, severity, confidence, evidence, next_test):
        nonlocal fid
        fid += 1
        findings.append({
            "id": f"F{fid}",
            "title": title,
            "severity": severity,
            "confidence": confidence,
            "evidence": evidence,
            "recommended_next_test": next_test,
        })

    # ── 1. Missing Security Headers ──
    missing = [h for h in SECURITY_HEADERS
               if h not in report.get("security_headers", {})]
    if missing:
        _add(
            f"Missing security headers: {', '.join(missing[:5])}",
            "medium", "high",
            f"Headers not present on root page response",
            "Verify header absence with `curl -I` and assess clickjacking / MIME-sniffing risk",
        )

    # ── 2. Forms without CSRF tokens ──
    for form in report.get("forms", []):
        input_names = [i["name"].lower() for i in form.get("inputs", [])]
        has_csrf = any(
            "csrf" in n or "token" in n or "_token" in n
            for n in input_names
        )
        if not has_csrf and form["method"] == "POST":
            _add(
                f"POST form without CSRF token at {form.get('found_on', '?')}",
                "high", "medium",
                f"Form action: {form['action']}, inputs: {input_names}",
                "Test for CSRF by replaying the POST without a token from a different origin",
            )

    # ── 3. Injection surfaces (forms with text inputs) ──
    for form in report.get("forms", []):
        text_inputs = [
            i for i in form.get("inputs", [])
            if i["type"] in ("text", "search", "email", "password",
                             "tel", "url", "hidden", "")
            and i["name"]
        ]
        if text_inputs:
            names = [i["name"] for i in text_inputs[:5]]
            _add(
                f"Potential injection surface: {', '.join(names)}",
                "high", "medium",
                f"Form at {form.get('found_on', '?')} ({form['method']} {form['action']})",
                f"Test parameters [{', '.join(names)}] for SQLi with single-quote and "
                f"time-based payloads; test for XSS with <script>alert(1)</script>",
            )

    # ── 4. Exposed sensitive paths ──
    sensitive_found = []
    for pg in report.get("pages", []):
        path = urlparse(pg["url"]).path
        if pg["status"] == 200 and any(
            s in path for s in [".git", ".env", "backup", ".DS_Store",
                                ".htaccess", "debug", "console",
                                "server-status", "server-info"]
        ):
            sensitive_found.append(path)

    if sensitive_found:
        _add(
            f"Exposed sensitive path(s): {', '.join(sensitive_found[:5])}",
            "critical", "high",
            f"HTTP 200 returned for sensitive paths",
            "Review file contents for credentials, config values, or source code leaks",
        )

    # ── 5. Directory listing detected ──
    for pg in report.get("pages", []):
        text = pg.get("visible_text", "").lower()
        if pg["status"] == 200 and ("index of" in text or "directory listing" in text):
            _add(
                f"Directory listing enabled at {pg['url']}",
                "medium", "high",
                f"Page text contains 'Index of' or 'directory listing'",
                "Browse listed files for sensitive data, backup archives, or source code",
            )

    # ── 6. Cookie security issues ──
    for pg in report.get("pages", []):
        for cookie in pg.get("cookies", []):
            issues = []
            if not cookie.get("httpOnly"):
                issues.append("missing HttpOnly")
            if not cookie.get("secure"):
                issues.append("missing Secure")
            if cookie.get("sameSite", "None") == "None":
                issues.append("SameSite=None")
            if issues:
                _add(
                    f"Insecure cookie '{cookie['name']}': {', '.join(issues)}",
                    "medium", "high",
                    f"Cookie set on {pg['url']}",
                    "Assess session hijacking risk via XSS or network sniffing",
                )
        break  # only check cookies once

    # ── 7. IDOR candidates (numeric IDs in URLs) ──
    numeric_urls = []
    for pg in report.get("pages", []):
        if re.search(r'[?&/](id|user|uid|item|order|doc|file)=?\d+', pg["url"], re.I):
            numeric_urls.append(pg["url"])
    if numeric_urls:
        _add(
            f"Possible IDOR: numeric identifiers in {len(numeric_urls)} URL(s)",
            "high", "medium",
            f"Example: {numeric_urls[0]}",
            "Try incrementing/decrementing the numeric ID while authenticated as a different user",
        )

    # ── 8. API endpoints discovered ──
    if report.get("endpoints"):
        _add(
            f"Discovered {len(report['endpoints'])} API endpoint(s) from JS analysis",
            "info", "medium",
            f"e.g. {', '.join(list(report['endpoints'])[:5])}",
            "Enumerate these endpoints with curl; check for auth bypass and parameter fuzzing",
        )

    # ── 9. Downloadable / loot-worthy files ──
    all_loot = []
    for pg in report.get("pages", []):
        for dl in pg.get("downloadable_files", []):
            if dl not in all_loot:
                all_loot.append(dl)
    if all_loot:
        _add(
            f"Downloadable files found: {', '.join(all_loot[:5])}",
            "high", "high",
            f"{len(all_loot)} file(s) with interesting extensions linked from the site",
            "Use download_file tool to grab these, then analyze_pcap for .pcap files "
            "or read_file for text-based files. Look for credentials, configs, or backups.",
        )

    # Sort findings by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 5))

    return findings


# ──────────────────────────────────────────────
# Human-Readable Summary Builder
# ──────────────────────────────────────────────

def _build_summary(report: dict) -> str:
    """Build a compact, LLM-friendly bullet-point summary."""
    lines = [
        f"## Web Recon Report: {report['target']}",
        f"*Crawled {report['pages_crawled']} page(s) at "
        f"{report['timestamp']}*\n",
    ]

    # Page overview
    status_codes = {}
    for pg in report["pages"]:
        s = pg.get("status", -1)
        status_codes[s] = status_codes.get(s, 0) + 1
    status_str = ", ".join(f"{code}: {n}" for code, n in sorted(status_codes.items()))
    lines.append(f"- **HTTP status spread:** {status_str}")
    lines.append(f"- **Forms discovered:** {len(report['forms'])}")
    lines.append(f"- **API endpoints from JS:** {len(report['endpoints'])}")

    # Security headers
    missing = [h for h in SECURITY_HEADERS
               if h not in report.get("security_headers", {})]
    if missing:
        lines.append(f"- **Missing security headers:** {', '.join(missing[:4])}...")
    else:
        lines.append("- **Security headers:** All key headers present ✅")

    # Findings
    if report["findings"]:
        lines.append(f"\n### Findings ({len(report['findings'])})")
        for f in report["findings"]:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡",
                    "low": "🔵", "info": "⚪"}.get(f["severity"], "❓")
            lines.append(f"- {icon} **[{f['severity'].upper()}]** {f['title']}")
    else:
        lines.append("\n### No vulnerability hypotheses generated.")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Compact Report (for LLM consumption)
# ──────────────────────────────────────────────

def compact_report(report: dict) -> dict:
    """
    Strip the heavy page evidence from a report, returning only
    what the LLM needs: summary + findings + high-level stats.
    """
    return {
        "target": report["target"],
        "timestamp": report["timestamp"],
        "pages_crawled": report["pages_crawled"],
        "forms_count": len(report.get("forms", [])),
        "endpoints": report.get("endpoints", []),
        "security_headers": report.get("security_headers", {}),
        "findings": report.get("findings", []),
        "summary": report.get("summary", ""),
    }


# ──────────────────────────────────────────────
# CLI entry point for standalone testing
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1"
    max_pg = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    print(f"🦞 Clawd Web Recon — scanning {url} (max {max_pg} pages)...\n")
    result = run_web_recon(url, max_pages=max_pg, headless=True)

    # Print summary
    print(result["summary"])
    print("\n" + "=" * 60)
    print(f"Full report: {len(json.dumps(result))} bytes")
    print(f"Findings: {len(result['findings'])}")
    print(f"Screenshots: {SCREENSHOT_DIR}")

    # Save full report
    out_path = os.path.join(config.WORKSPACE_DIR, "web_recon_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Saved to: {out_path}")
