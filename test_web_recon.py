"""Quick integration test — verify web_recon is properly wired into executor."""
import sys, io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import executor
import json

print("Testing executor.web_recon integration...")
result = executor.web_recon("http://127.0.0.1", max_pages=3)

print(f"Keys: {list(result.keys())}")
print(f"Target: {result.get('target')}")
print(f"Pages: {result.get('pages_crawled')}")
print(f"Findings: {len(result.get('findings', []))}")
print(f"Summary:\n{result.get('summary', 'N/A')}")

# Check compact report doesn't contain heavy page evidence
assert "pages" not in result, "Compact report should NOT contain 'pages' array"
assert "summary" in result, "Compact report must have 'summary'"
assert "findings" in result, "Compact report must have 'findings'"

print("\n✅ Integration test PASSED — compact report is LLM-ready")
