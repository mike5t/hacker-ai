"""Integration test — verify new tools (idor_enum, download_file, analyze_pcap) are properly wired."""
import sys, io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import executor
import json

# ── 1. Check idor_enum exists and validates input ──
print("1. Testing idor_enum input validation...")
result = executor.idor_enum("http://127.0.0.1/page")  # no {FUZZ}
assert "error" in result, "Should fail without {FUZZ} placeholder"
print(f"   ✅ Correctly rejected URL without {{FUZZ}}: {result['error'][:60]}")

# ── 2. Check download_file exists and runs ──
print("\n2. Testing download_file...")
result = executor.download_file("http://127.0.0.1:99999/nonexistent.txt")
assert isinstance(result, dict), "Should return a dict"
assert "success" in result, "Must have 'success' key"
print(f"   ✅ download_file returned: success={result.get('success')}, error={result.get('error', 'none')[:60]}")

# ── 3. Check analyze_pcap exists and handles missing file ──
print("\n3. Testing analyze_pcap with missing file...")
result = executor.analyze_pcap("nonexistent.pcap")
assert isinstance(result, dict), "Should return a dict"
assert result.get("success") == False, "Should fail for missing file"
assert "error" in result, "Must have 'error' key"
print(f"   ✅ Correctly reported missing file: {result['error'][:60]}")

# ── 4. Check all three are registered in engine.py ──
print("\n4. Checking engine.py registration...")
from engine import TOOLS, TOOL_FUNCTIONS

tool_names = {t["function"]["name"] for t in TOOLS}
for name in ["idor_enum", "download_file", "analyze_pcap"]:
    assert name in tool_names, f"{name} not in TOOLS array"
    assert name in TOOL_FUNCTIONS, f"{name} not in TOOL_FUNCTIONS dict"
    print(f"   ✅ {name} registered in TOOLS and TOOL_FUNCTIONS")

# ── 5. Check web_recon has downloadable_files detection ──
print("\n5. Checking web_recon downloadable file detection...")
import web_recon as wr
import inspect
source = inspect.getsource(wr._visit_page)
assert "downloadable_files" in source, "_visit_page should detect downloadable files"
assert "LOOT_EXTENSIONS" in source, "_visit_page should have LOOT_EXTENSIONS"
print("   ✅ _visit_page has downloadable file detection")

findings_source = inspect.getsource(wr._generate_findings)
assert "downloadable_files" in findings_source or "Downloadable" in findings_source, \
    "_generate_findings should flag downloadable files"
print("   ✅ _generate_findings has downloadable file finding rule")

print("\n" + "=" * 50)
print("🦞 ALL INTEGRATION TESTS PASSED — New tools are ready!")
print("=" * 50)
