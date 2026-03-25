#!/usr/bin/env python3
"""
Knowledge Retention HTTP Server
Minimal JSON API for the review web UI. Uses stdlib only.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from retention import (
    SM2, CardGenerator, RelevanceScorer, ConnectionFinder,
    load_review_state, save_review_state, load_cards, save_cards,
    load_all_cards, load_paper, load_all_papers, load_interests,
    register_cards, run_daily_check,
)

import yaml

BASE_DIR = Path(__file__).parent
PORT = int(os.environ.get("RETENTION_PORT", 8234))
TTS_CACHE_DIR = BASE_DIR / ".tts_cache"
TTS_VOICE = "en-US-AriaNeural"  # Natural-sounding Microsoft neural voice

# Locks for thread safety
_review_state_lock = threading.Lock()
_feedback_lock = threading.Lock()
_topics_lock = threading.Lock()


def json_response(handler, data, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))


def read_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except (ValueError, TypeError):
        return {}
    if length <= 0 or length > 10 * 1024 * 1024:  # 10MB max
        return {}
    try:
        return json.loads(handler.rfile.read(length).decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def sanitize_paper_id(paper_id):
    """Reject paper IDs with path traversal characters."""
    if not paper_id or '..' in paper_id or '/' in paper_id or '\\' in paper_id:
        return None
    return paper_id


class RetentionHandler(SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Serve review.html at root
        if path == "" or path == "/":
            self.serve_file("review.html", "text/html")
            return

        # API routes
        if path == "/api/dashboard":
            self.api_dashboard()
        elif path == "/api/review/due":
            self.api_review_due()
        elif path == "/api/review/stats":
            self.api_review_stats()
        elif path == "/api/radio/playlist":
            self.api_radio_playlist()
        elif path == "/api/papers":
            self.api_papers()
        elif path == "/api/topics":
            self.api_get_topics()
        elif path.startswith("/api/papers/"):
            paper_id = sanitize_paper_id(path[len("/api/papers/"):])
            if not paper_id:
                json_response(self, {"error": "Invalid paper ID"}, 400)
            else:
                self.api_paper_detail(paper_id)
        elif path.startswith("/api/cards/"):
            paper_id = sanitize_paper_id(path[len("/api/cards/"):])
            if not paper_id:
                json_response(self, {"error": "Invalid paper ID"}, 400)
            else:
                self.api_cards(paper_id)
        elif path.startswith("/api/connections/"):
            paper_id = sanitize_paper_id(path[len("/api/connections/"):])
            if not paper_id:
                json_response(self, {"error": "Invalid paper ID"}, 400)
            else:
                self.api_connections(paper_id)
        else:
            # Silently return 404 for favicon etc
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/review/answer":
            self.api_review_answer()
        elif path == "/api/tts":
            self.api_tts()
        elif path == "/api/feedback":
            self.api_save_feedback()
        elif path == "/api/topics":
            self.api_add_topic()
        elif path == "/api/papers/add-url":
            self.api_add_paper_url()
        elif path.startswith("/api/cards/generate/"):
            paper_id = sanitize_paper_id(path[len("/api/cards/generate/"):])
            if not paper_id:
                json_response(self, {"error": "Invalid paper ID"}, 400)
            else:
                self.api_generate_cards(paper_id)
        elif path.startswith("/api/papers/") and path.endswith("/status"):
            paper_id = sanitize_paper_id(path[len("/api/papers/"):-len("/status")])
            if not paper_id:
                json_response(self, {"error": "Invalid paper ID"}, 400)
            else:
                self.api_update_status(paper_id)
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path.startswith("/api/topics/"):
            topic_id = path[len("/api/topics/"):]
            self.api_delete_topic(topic_id)
        else:
            self.send_response(404)
            self.end_headers()

    def serve_file(self, filename, content_type):
        filepath = BASE_DIR / filename
        if not filepath.exists():
            self.send_error(404, f"{filename} not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.end_headers()
        with open(filepath, 'rb') as f:
            self.wfile.write(f.read())

    # ---- API Handlers ----

    def api_dashboard(self):
        state = load_review_state()
        stats = SM2.get_stats(state)
        due_ids = SM2.get_due_cards(state)

        papers = load_all_papers()
        all_cards = load_all_cards()

        # Reading pipeline counts
        pipeline = {}
        for p in papers.values():
            status = p.get('status', 'unread')
            pipeline[status] = pipeline.get(status, 0) + 1

        # Due cards with paper context
        due_cards = []
        for card_id in due_ids:
            card_state = state["cards"][card_id]
            paper_id = card_state["paper_id"]
            paper_cards = all_cards.get(paper_id, {}).get("cards", [])
            card_data = next((c for c in paper_cards if c["id"] == card_id), None)
            if card_data:
                due_cards.append({
                    **card_data,
                    "paper_id": paper_id,
                    "paper_title": papers.get(paper_id, {}).get("title", "Unknown"),
                    "ease_factor": card_state["ease_factor"],
                    "repetitions": card_state["repetitions"],
                    "interval_days": card_state["interval_days"],
                })

        json_response(self, {
            "stats": stats,
            "pipeline": pipeline,
            "due_cards": due_cards,
            "total_papers": len(papers),
            "papers_with_cards": len(all_cards),
        })

    def api_review_due(self):
        state = load_review_state()
        due_ids = SM2.get_due_cards(state)
        papers = load_all_papers()
        all_cards = load_all_cards()

        due_cards = []
        for card_id in due_ids:
            card_state = state["cards"][card_id]
            paper_id = card_state["paper_id"]
            paper_cards = all_cards.get(paper_id, {}).get("cards", [])
            card_data = next((c for c in paper_cards if c["id"] == card_id), None)
            if card_data:
                due_cards.append({
                    **card_data,
                    "paper_id": paper_id,
                    "paper_title": papers.get(paper_id, {}).get("title", "Unknown"),
                    "ease_factor": card_state["ease_factor"],
                    "repetitions": card_state["repetitions"],
                    "interval_days": card_state["interval_days"],
                })

        json_response(self, {"due_cards": due_cards, "count": len(due_cards)})

    def api_review_stats(self):
        state = load_review_state()
        stats = SM2.get_stats(state)
        json_response(self, stats)

    def api_review_answer(self):
        body = read_body(self)
        card_id = body.get("card_id")
        quality = body.get("quality")

        if not card_id or quality is None:
            json_response(self, {"error": "card_id and quality required"}, 400)
            return

        try:
            quality = int(quality)
        except (ValueError, TypeError):
            json_response(self, {"error": "quality must be a number"}, 400)
            return

        with _review_state_lock:
            state = load_review_state()
            if card_id not in state.get("cards", {}):
                json_response(self, {"error": f"Card not found: {card_id}"}, 404)
                return

            SM2.schedule(state["cards"][card_id], quality)
            save_review_state(state)

        json_response(self, {
            "ok": True,
            "next_review": state["cards"][card_id]["next_review"],
            "interval_days": state["cards"][card_id]["interval_days"],
        })

    def api_papers(self):
        papers = load_all_papers()
        all_cards = load_all_cards()
        state = load_review_state()

        result = []
        for pid, p in sorted(papers.items(), key=lambda x: x[1].get('added_at', ''), reverse=True):
            card_count = len(all_cards.get(pid, {}).get("cards", []))
            # Count due cards for this paper
            due_count = sum(
                1 for cid, cs in state.get("cards", {}).items()
                if cs["paper_id"] == pid and cs["next_review"] <= datetime.now().strftime("%Y-%m-%d")
            )
            result.append({
                "id": pid,
                "title": p.get("title", "Untitled"),
                "authors": p.get("authors", []),
                "year": p.get("year"),
                "status": p.get("status", "unread"),
                "interest_level": p.get("interest_level"),
                "tags": p.get("tags", []),
                "categories": p.get("categories", []),
                "card_count": card_count,
                "due_count": due_count,
                "relevance_score": p.get("relevance_score"),
                "discovery_reason": p.get("discovery_reason", ""),
                "citation_count": p.get("citation_count"),
                "added_at": p.get("added_at"),
            })

        json_response(self, {"papers": result})

    def api_paper_detail(self, paper_id):
        paper = load_paper(paper_id)
        if not paper:
            json_response(self, {"error": "Paper not found"}, 404)
            return
        cards = load_cards(paper_id)
        connections = ConnectionFinder.find_connections(paper_id)
        json_response(self, {
            "paper": paper,
            "cards": cards,
            "connections": connections,
        })

    def api_cards(self, paper_id):
        cards = load_cards(paper_id)
        if not cards:
            json_response(self, {"error": "No cards found"}, 404)
            return
        json_response(self, cards)

    def api_connections(self, paper_id):
        connections = ConnectionFinder.find_connections(paper_id)
        json_response(self, {"connections": connections})

    def api_generate_cards(self, paper_id):
        paper = load_paper(paper_id)
        if not paper:
            json_response(self, {"error": "Paper not found"}, 404)
            return

        # Get related papers for connection cards
        all_papers = load_all_papers()
        related = [p for pid, p in all_papers.items() if pid != paper_id]

        cards_data = CardGenerator.generate(paper, related)
        if cards_data is None:
            json_response(self, {"error": "Card generation failed"}, 500)
            return

        save_cards(paper_id, cards_data)
        count = register_cards(cards_data)

        json_response(self, {
            "ok": True,
            "paper_id": paper_id,
            "cards_generated": count,
            "cards": cards_data,
        })

    def api_update_status(self, paper_id):
        body = read_body(self)
        new_status = body.get("status")
        if not new_status:
            json_response(self, {"error": "status required"}, 400)
            return

        VALID_STATUSES = {'discovered', 'queued', 'unread', 'reading', 'read', 'reviewing', 'mastered'}
        if new_status not in VALID_STATUSES:
            json_response(self, {"error": f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}"}, 400)
            return

        paper_path = BASE_DIR / "papers" / f"{paper_id}.yaml"
        if not paper_path.exists():
            json_response(self, {"error": "Paper not found"}, 404)
            return

        with open(paper_path, 'r') as f:
            paper = yaml.safe_load(f) or {}

        paper['status'] = new_status
        # Add status history
        if 'status_history' not in paper:
            paper['status_history'] = []
        paper['status_history'].append({
            'status': new_status,
            'at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        with open(paper_path, 'w') as f:
            yaml.dump(paper, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        json_response(self, {"ok": True, "status": new_status})

    # ---- Discover / Feedback / Topics ----

    def _load_feedback(self):
        path = BASE_DIR / "feedback.json"
        if path.exists():
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"votes": {}, "priorities": {}}

    def _save_feedback(self, data):
        with open(BASE_DIR / "feedback.json", 'w') as f:
            json.dump(data, f, indent=2)

    def _load_topics(self):
        path = BASE_DIR / "topics.json"
        if path.exists():
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"topics": []}

    def _save_topics(self, data):
        with open(BASE_DIR / "topics.json", 'w') as f:
            json.dump(data, f, indent=2)

    def api_get_topics(self):
        data = self._load_topics()
        json_response(self, data)

    def api_add_topic(self):
        body = read_body(self)
        desc = body.get("description", "").strip()
        priority = body.get("priority", "relevant")
        if not desc:
            json_response(self, {"error": "description required"}, 400)
            return

        with _topics_lock:
            data = self._load_topics()
            topic_id = f"topic-{int(datetime.now().timestamp() * 1000)}"
            data["topics"].append({
                "id": topic_id,
                "description": desc,
                "priority": priority,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            self._save_topics(data)
        json_response(self, {"ok": True, "id": topic_id})

    def api_delete_topic(self, topic_id):
        with _topics_lock:
            data = self._load_topics()
            before = len(data["topics"])
            data["topics"] = [t for t in data["topics"] if t.get("id") != topic_id]
            self._save_topics(data)
        removed = before - len(data["topics"])
        json_response(self, {"ok": True, "removed": removed})

    def api_save_feedback(self):
        body = read_body(self)
        paper_id = body.get("paper_id")
        if not paper_id:
            json_response(self, {"error": "paper_id required"}, 400)
            return

        with _feedback_lock:
            fb = self._load_feedback()
            if body.get("vote"):
                fb["votes"][paper_id] = body["vote"]
            if body.get("priority"):
                fb["priorities"][paper_id] = body["priority"]
            self._save_feedback(fb)
        json_response(self, {"ok": True})

    def api_add_paper_url(self):
        body = read_body(self)
        url = body.get("url", "").strip()
        if not url:
            json_response(self, {"error": "url required"}, 400)
            return

        from retention import scrape_web_page
        from papers_cli import get_paper_id, save_yaml, PAPERS_DIR, fetch_arxiv_metadata

        if 'arxiv.org' in url:
            metadata = fetch_arxiv_metadata(url)
            if not metadata:
                json_response(self, {"error": "Could not fetch arXiv metadata"}, 400)
                return
            paper_id = get_paper_id(metadata['title'], metadata['year'])
            paper_path = PAPERS_DIR / f"{paper_id}.yaml"
            if paper_path.exists():
                json_response(self, {"error": "Paper already exists", "id": paper_id}, 400)
                return
            paper_data = {
                'title': metadata['title'],
                'authors': metadata['authors'],
                'year': metadata['year'],
                'abstract': metadata['abstract'],
                'summary': metadata['abstract'],
                'url': metadata['url'],
                'status': 'queued',
                'source_type': 'arxiv',
                'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            scraped = scrape_web_page(url)
            if not scraped:
                json_response(self, {"error": "Could not fetch page"}, 400)
                return
            title = scraped['title']
            paper_id = get_paper_id(title, datetime.now().year)
            paper_path = PAPERS_DIR / f"{paper_id}.yaml"
            if paper_path.exists():
                json_response(self, {"error": "Already exists", "id": paper_id}, 400)
                return
            desc = (scraped['description'] or scraped['text'][:300] or '').strip()
            paper_data = {
                'title': title,
                'authors': [],
                'year': datetime.now().year,
                'abstract': desc,
                'summary': desc,
                'url': url,
                'status': 'queued',
                'source_type': 'web',
                'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        paper_data = {k: v for k, v in paper_data.items() if v}
        PAPERS_DIR.mkdir(exist_ok=True)
        save_yaml(paper_path, paper_data)
        json_response(self, {"ok": True, "id": paper_id, "title": paper_data.get('title', '')})

    def api_radio_playlist(self):
        """Generate a narrative playlist for passive audio learning.
        Structured as mini-lectures per paper, not Q&A flashcards."""
        papers = load_all_papers()
        all_cards = load_all_cards()

        segments = []

        # 1. Paper deep-dives — intro + key ideas woven into narrative
        for pid, p in papers.items():
            if pid not in all_cards:
                continue

            title = p.get('title', 'Unknown')
            summary = p.get('summary') or p.get('abstract', '')
            cards = all_cards[pid].get('cards', [])

            if not summary and not cards:
                continue

            # Opening: what is this paper about
            intro = f"Let's talk about {title}. "
            if summary:
                intro += summary
            segments.append({
                "type": "summary",
                "label": title[:55],
                "text": intro,
                "pause_after": 3,
            })

            # Key ideas: narrative style, not Q&A
            for card in cards:
                q = card.get('question', '')
                a = card.get('answer', '')
                ctype = card.get('type', 'concept')
                if not q or not a:
                    continue

                prefixes = {
                    'concept': "A key idea here:",
                    'comparison': "To put this in context:",
                    'application': "In practice,",
                    'limitation': "It's worth noting that",
                    'connection': "Interestingly,",
                }
                prefix = prefixes.get(ctype, "")
                text = f"{prefix} {q} {a}"

                segments.append({
                    "type": ctype,
                    "label": f"{ctype.title()} — {title[:40]}",
                    "text": text,
                    "pause_after": 3,
                })

        # 2. New discoveries — brief introductions
        discovered = [(pid, p) for pid, p in papers.items() if p.get("status") == "discovered"]
        discovered.sort(key=lambda x: x[1].get("relevance_score", 0), reverse=True)
        for pid, p in discovered[:5]:
            abstract = p.get("abstract", "")
            if abstract:
                segments.append({
                    "type": "discovery",
                    "label": f"New — {p.get('title', 'Unknown')[:50]}",
                    "text": f"Here's a recently discovered paper: {p.get('title', 'Unknown')}. {abstract[:400]}",
                    "pause_after": 3,
                })

        json_response(self, {
            "segments": segments,
            "total": len(segments),
            "estimated_minutes": round(len(segments) * 0.4, 1),
        })

    def api_tts(self):
        """Generate speech audio from text using edge-tts."""
        body = read_body(self)
        text = body.get("text", "").strip()
        if not text:
            json_response(self, {"error": "text required"}, 400)
            return

        # Cache by text hash
        TTS_CACHE_DIR.mkdir(exist_ok=True)
        text_hash = hashlib.md5(text.encode()).hexdigest()
        cache_path = TTS_CACHE_DIR / f"{text_hash}.mp3"

        if not cache_path.exists():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "edge_tts",
                     "--voice", TTS_VOICE,
                     "--text", text,
                     "--write-media", str(cache_path)],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode != 0:
                    json_response(self, {"error": f"TTS failed: {result.stderr}"}, 500)
                    return
            except Exception as e:
                json_response(self, {"error": str(e)}, 500)
                return

        # Serve the mp3
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=86400")
        with open(cache_path, 'rb') as f:
            data = f.read()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        # Quieter logging - suppress API request logs
        try:
            msg = format % args
            if "/api/" in msg:
                return
        except Exception:
            pass
        super().log_message(format, *args)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    server = ThreadedHTTPServer(("127.0.0.1", PORT), RetentionHandler)
    print(f"Knowledge Retention Server running at http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
