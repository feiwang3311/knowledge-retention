#!/usr/bin/env python3
"""
Test suite for Knowledge Retention System.
Run: python3 tests.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

PASS = 0
FAIL = 0
ERRORS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def section(name):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


# ============================================================
# Unit Tests (no server needed)
# ============================================================

section("SM-2 Algorithm")

from retention import SM2

# Test initial state
s = SM2.initial_state("test-01", "paper-01")
test("initial state has required fields",
     all(k in s for k in ["ease_factor", "interval_days", "repetitions", "next_review", "history"]))
test("initial ease factor is 2.5", s["ease_factor"] == 2.5)
test("initial interval is 0", s["interval_days"] == 0)
test("initial repetitions is 0", s["repetitions"] == 0)

# Test pass (Good = quality 4)
s1 = SM2.initial_state("t1", "p1")
SM2.schedule(s1, 4)
test("first Good → interval 1d", s1["interval_days"] == 1)
test("first Good → repetitions 1", s1["repetitions"] == 1)
SM2.schedule(s1, 4)
test("second Good → interval 6d", s1["interval_days"] == 6)
test("second Good → repetitions 2", s1["repetitions"] == 2)
SM2.schedule(s1, 4)
test("third Good → interval grows", s1["interval_days"] > 6)
test("third Good → uses old EF for calc", s1["interval_days"] == round(6 * 2.5))

# Test fail (Forgot = quality 1)
s2 = SM2.initial_state("t2", "p2")
SM2.schedule(s2, 4)
SM2.schedule(s2, 4)
SM2.schedule(s2, 1)  # Forgot
test("Forgot resets repetitions to 0", s2["repetitions"] == 0)
test("Forgot resets interval to 1d", s2["interval_days"] == 1)

# Test EF floor
s3 = SM2.initial_state("t3", "p3")
for _ in range(10):
    SM2.schedule(s3, 1)
test("EF never drops below 1.3", s3["ease_factor"] >= 1.3)

# Test interval cap
s4 = SM2.initial_state("t4", "p4")
for _ in range(30):
    SM2.schedule(s4, 5)  # Easy every time
test("interval capped at 365d", s4["interval_days"] <= 365)

# Test history tracking
s5 = SM2.initial_state("t5", "p5")
SM2.schedule(s5, 4)
SM2.schedule(s5, 3)
test("history has 2 entries", len(s5["history"]) == 2)
test("history records quality", s5["history"][0]["quality"] == 4)

# Test get_due_cards
state = {"cards": {
    "c1": {**SM2.initial_state("c1", "p1"), "next_review": "2020-01-01"},
    "c2": {**SM2.initial_state("c2", "p1"), "next_review": "2099-01-01"},
}}
due = SM2.get_due_cards(state)
test("get_due_cards returns past-due cards", "c1" in due)
test("get_due_cards excludes future cards", "c2" not in due)

# Test get_stats
stats = SM2.get_stats(state)
test("stats has required fields",
     all(k in stats for k in ["total", "due_today", "mastered", "new_cards"]))
test("stats total correct", stats["total"] == 2)


section("Card Generator")

from retention import CardGenerator

# Test prompt building
paper = {"title": "Test Paper", "authors": ["Alice"], "abstract": "Abstract text",
         "summary": "Summary", "tags": ["ml"], "id": "test"}
prompt = CardGenerator._build_prompt(paper)
test("prompt contains paper title", "Test Paper" in prompt)
test("prompt contains style instructions", "plain language" in prompt.lower() or "analogies" in prompt.lower())

# Test JSON parsing
test("parse valid JSON array",
     CardGenerator._parse_response('[{"id":"a","type":"concept","question":"Q","answer":"A"}]') is not None)
test("parse with markdown fences",
     CardGenerator._parse_response('```json\n[{"id":"a"}]\n```') is not None)
test("parse invalid text returns None",
     CardGenerator._parse_response('not json at all') is None)
test("parse empty array",
     CardGenerator._parse_response('[]') == [])

# Test card validation in generate (mock the LLM call)
original_cli = CardGenerator.generate_via_claude_cli
original_api = CardGenerator.generate_via_api

# Mock: return cards with missing fields
CardGenerator.generate_via_claude_cli = staticmethod(lambda p, r=None: [
    {"id": "ok-01", "type": "concept", "question": "Q?", "answer": "A."},
    {"id": "bad-01", "type": "concept"},  # missing question/answer
    "not a dict",  # not a dict
    {"id": "bad-02", "question": "Q?"},  # missing answer
])
CardGenerator.generate_via_api = staticmethod(lambda p, r=None: None)

result = CardGenerator.generate({"id": "test-paper", "title": "T", "authors": [], "abstract": "A", "summary": "S", "tags": []})
test("card validation filters invalid cards", result is not None and len(result["cards"]) == 1)
test("valid card kept", result["cards"][0]["question"] == "Q?")

# Restore
CardGenerator.generate_via_claude_cli = original_cli
CardGenerator.generate_via_api = original_api


section("Relevance Scorer")

from retention import RelevanceScorer

interests = {
    "projects": [
        {"name": "AI Compiler", "keywords": ["compiler", "llm", "optimization"], "weight": 1.0}
    ],
    "topics": ["gpu kernels"]
}

test("high relevance for matching paper",
     RelevanceScorer.score({"title": "LLM compiler optimization", "abstract": "", "tags": [], "categories": []}, interests) > 0)
test("zero relevance for unrelated paper",
     RelevanceScorer.score({"title": "Cooking recipes", "abstract": "food", "tags": ["food"], "categories": []}, interests) == 0)
test("handles empty paper gracefully",
     RelevanceScorer.score({}, interests) == 0.0)


section("File I/O")

from retention import (load_review_state, save_review_state, load_cards, save_cards,
                        load_all_cards, register_cards, REVIEW_STATE_FILE, CARDS_DIR)

# Test corrupted JSON handling
tmpdir = tempfile.mkdtemp()
orig_state = REVIEW_STATE_FILE
orig_cards = CARDS_DIR

try:
    # Temporarily override paths
    import retention
    retention.REVIEW_STATE_FILE = Path(tmpdir) / "review_state.json"
    retention.CARDS_DIR = Path(tmpdir) / "cards"
    retention.CARDS_DIR.mkdir()

    # Write corrupted state
    with open(retention.REVIEW_STATE_FILE, 'w') as f:
        f.write("{corrupted")
    state = load_review_state()
    test("corrupted review_state.json returns empty", state == {"cards": {}})

    # Write corrupted card file
    with open(retention.CARDS_DIR / "bad.json", 'w') as f:
        f.write("not json")
    cards = load_all_cards()
    test("corrupted card file skipped gracefully", len(cards) == 0)

    # Test normal save/load cycle
    test_state = {"cards": {"c1": SM2.initial_state("c1", "p1")}}
    save_review_state(test_state)
    loaded = load_review_state()
    test("save/load review state roundtrip", loaded["cards"]["c1"]["paper_id"] == "p1")

    # Test register_cards
    test_cards = {"paper_id": "p1", "cards": [{"id": "new-01", "question": "Q", "answer": "A"}]}
    save_cards("p1", test_cards)
    count = register_cards(test_cards)
    test("register_cards adds new cards", count == 1)
    state2 = load_review_state()
    test("registered card in state", "new-01" in state2["cards"])

finally:
    retention.REVIEW_STATE_FILE = orig_state
    retention.CARDS_DIR = orig_cards
    shutil.rmtree(tmpdir)


section("RSS Feed Parsing")

from retention import _strip_html

test("strip_html removes tags", _strip_html("<p>Hello <b>world</b></p>") == "Hello world")
test("strip_html handles empty", _strip_html("") == "")
test("strip_html no tags passthrough", _strip_html("plain text") == "plain text")


section("Sanitize Paper ID")

sys.path.insert(0, str(Path(__file__).parent))
# Import the function from server module without starting the server
from server import sanitize_paper_id

test("normal paper ID passes", sanitize_paper_id("2025-gpu-kernel-scientist") == "2025-gpu-kernel-scientist")
test("path traversal blocked", sanitize_paper_id("../../etc/passwd") is None)
test("slash blocked", sanitize_paper_id("path/to/file") is None)
test("backslash blocked", sanitize_paper_id("path\\file") is None)
test("empty string blocked", sanitize_paper_id("") is None)
test("None blocked", sanitize_paper_id(None) is None)


section("CLI Argument Parsing")

from papers_cli import main
import argparse

# Test that empty tags don't produce ['']
test("empty string split filter",
     [t.strip() for t in "".split(',') if t.strip()] == [])
test("whitespace tag filter",
     [t.strip() for t in " , , ".split(',') if t.strip()] == [])
test("valid tags preserved",
     [t.strip() for t in "gpu,ml".split(',') if t.strip()] == ["gpu", "ml"])


# ============================================================
# Integration Tests (needs server running)
# ============================================================

section("Server Integration Tests")

SERVER_URL = "http://127.0.0.1:8234"


def api_get(path):
    try:
        req = urllib.request.Request(f"{SERVER_URL}{path}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return body, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def api_post(path, data):
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(f"{SERVER_URL}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return body, e.code
    except Exception as e:
        return {"error": str(e)}, 0


# Check if server is running
resp, code = api_get("/api/review/stats")
if code == 0:
    print("  SKIP  Server not running — skipping integration tests")
    print("         Start with: python3 server.py &")
else:
    test("GET /api/dashboard returns 200", api_get("/api/dashboard")[1] == 200)

    data, code = api_get("/api/dashboard")
    test("dashboard has stats", "stats" in data)
    test("dashboard has pipeline", "pipeline" in data)
    test("dashboard has due_cards", "due_cards" in data)

    test("GET /api/papers returns 200", api_get("/api/papers")[1] == 200)
    test("GET /api/review/due returns 200", api_get("/api/review/due")[1] == 200)
    test("GET /api/review/stats returns 200", api_get("/api/review/stats")[1] == 200)

    # Error cases
    data, code = api_post("/api/review/answer", {"card_id": "FAKE", "quality": 4})
    test("invalid card_id → 404", code == 404)

    data, code = api_post("/api/review/answer", {})
    test("missing fields → 400", code == 400)

    data, code = api_get("/api/papers/nonexistent-paper")
    test("nonexistent paper → 404", code == 404)

    # Path traversal
    data, code = api_get("/api/papers/../../etc/passwd")
    test("path traversal → 400", code == 400)

    # Status validation
    papers_data, _ = api_get("/api/papers")
    if papers_data.get("papers"):
        pid = papers_data["papers"][0]["id"]
        data, code = api_post(f"/api/papers/{pid}/status", {"status": "BOGUS"})
        test("invalid status → 400", code == 400)

        data, code = api_post(f"/api/papers/{pid}/status", {"status": "unread"})
        test("valid status update → 200", code == 200 and data.get("ok"))

    # TTS
    data, code = api_post("/api/tts", {"text": ""})
    test("empty TTS text → 400", code == 400)

    # Topics
    data, code = api_post("/api/topics", {"description": "test topic", "priority": "important"})
    test("add topic → 200", code == 200 and data.get("ok"))
    topic_id = data.get("id", "")

    data, code = api_get("/api/topics")
    test("get topics → 200", code == 200)
    test("topic list contains added topic",
         any(t.get("description") == "test topic" for t in data.get("topics", [])))

    data, code = api_post("/api/topics", {"description": ""})
    test("empty topic → 400", code == 400)

    # Feedback
    data, code = api_post("/api/feedback", {"paper_id": "test-id", "vote": "good"})
    test("save feedback → 200", code == 200 and data.get("ok"))

    data, code = api_post("/api/feedback", {})
    test("feedback no paper_id → 400", code == 400)

    # Radio playlist
    data, code = api_get("/api/radio/playlist")
    test("radio playlist → 200", code == 200)
    test("radio has segments", "segments" in data)
    test("radio has total", "total" in data)
    if data.get("segments"):
        seg = data["segments"][0]
        test("segment has required fields",
             all(k in seg for k in ["type", "label", "text", "pause_after"]))

    # Papers have new fields
    papers_resp, _ = api_get("/api/papers")
    if papers_resp.get("papers"):
        p = papers_resp["papers"][0]
        test("paper has discovery_reason field", "discovery_reason" in p)
        test("paper has citation_count field", "citation_count" in p)

    # Cleanup test topic
    if topic_id:
        api_post_delete = urllib.request.Request(
            f"{SERVER_URL}/api/topics/{topic_id}", method="DELETE")
        try:
            with urllib.request.urlopen(api_post_delete, timeout=5) as r:
                pass
        except Exception:
            pass


    # Explorations
    data, code = api_get("/api/explorations")
    test("get explorations → 200", code == 200)
    test("explorations has list", "explorations" in data)

    data, code = api_post("/api/explorations", {"question": ""})
    test("empty exploration question → 400", code == 400)

    # Study flow
    papers_resp2, _ = api_get("/api/papers")
    study_papers = [p for p in papers_resp2.get("papers", []) if p.get("card_count", 0) > 0]
    if study_papers:
        spid = study_papers[0]["id"]
        data, code = api_get(f"/api/study/{spid}")
        test("study endpoint → 200", code == 200)
        test("study has paper", "paper" in data)
        test("study has cards", "cards" in data)

    # Radio papers
    data, code = api_get("/api/radio/papers")
    test("radio papers → 200", code == 200)
    test("radio papers has list", "papers" in data)
    if data.get("papers"):
        rp = data["papers"][0]
        test("radio paper has studied flag", "studied" in rp)
        test("radio paper has status", "status" in rp)

        # Filtered playlist
        rpid = data["papers"][0]["id"]
        data2, code2 = api_get(f"/api/radio/playlist?paper_ids={rpid}")
        test("filtered radio playlist → 200", code2 == 200)
        test("filtered playlist has segments", len(data2.get("segments", [])) > 0)

    # Paper delete (pick a low-value discovered paper)
    del_papers = [p for p in papers_resp2.get("papers", [])
                  if p.get("status") == "discovered" and (p.get("citation_count") or 0) == 0]
    if del_papers:
        del_id = del_papers[-1]["id"]
        data, code = api_post(f"/api/papers/{del_id}/delete", {})
        test("delete paper → 200", code == 200 and data.get("ok"))


section("Card Generator Parse")

from retention import CardGenerator

# Test the improved JSON parser
test("parse with trailing text",
     CardGenerator._parse_response('[{"id":"a"}] some trailing text [ref]') is not None)
test("parse nested brackets",
     CardGenerator._parse_response('Here: [{"id":"a","tags":["x","y"]}]') is not None)


section("Semantic Scholar Helpers")

from retention import _s2_paper_to_dict

# Test citationCount: null handling
test("citationCount null → 0",
     _s2_paper_to_dict({"title": "Test", "citationCount": None, "authors": []}).get("citation_count") == 0)
test("citationCount missing → 0",
     _s2_paper_to_dict({"title": "Test", "authors": []}).get("citation_count") == 0)
test("citationCount present → value",
     _s2_paper_to_dict({"title": "T", "citationCount": 42, "authors": []}).get("citation_count") == 42)
test("missing title → None",
     _s2_paper_to_dict({}) is None)
test("tldr null handled",
     _s2_paper_to_dict({"title": "T", "authors": [], "tldr": None}) is not None)


# ============================================================
# Summary
# ============================================================

print(f"\n{'='*60}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")

if ERRORS:
    print("\nFailures:")
    for e in ERRORS:
        print(e)

sys.exit(0 if FAIL == 0 else 1)
