#!/usr/bin/env python3
"""
Knowledge Retention HTTP Server
Minimal JSON API for the review web UI. Uses stdlib only.
"""

import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from retention import (
    SM2, CardGenerator, RelevanceScorer, ConnectionFinder,
    load_review_state, save_review_state, load_cards, save_cards,
    load_all_cards, load_paper, load_all_papers, load_interests,
    register_cards, run_daily_check,
)

BASE_DIR = Path(__file__).parent
PORT = int(os.environ.get("RETENTION_PORT", 8234))


def json_response(handler, data, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))


def read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode('utf-8'))


class RetentionHandler(SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
        elif path == "/api/papers":
            self.api_papers()
        elif path.startswith("/api/papers/"):
            paper_id = path[len("/api/papers/"):]
            self.api_paper_detail(paper_id)
        elif path.startswith("/api/cards/"):
            paper_id = path[len("/api/cards/"):]
            self.api_cards(paper_id)
        elif path.startswith("/api/connections/"):
            paper_id = path[len("/api/connections/"):]
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
        elif path.startswith("/api/cards/generate/"):
            paper_id = path[len("/api/cards/generate/"):]
            self.api_generate_cards(paper_id)
        elif path.startswith("/api/papers/") and path.endswith("/status"):
            paper_id = path[len("/api/papers/"):-len("/status")]
            self.api_update_status(paper_id)
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

        state = load_review_state()
        if card_id not in state["cards"]:
            json_response(self, {"error": f"Card not found: {card_id}"}, 404)
            return

        SM2.schedule(state["cards"][card_id], int(quality))
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
                if cs["paper_id"] == pid and cs["next_review"] <= __import__('datetime').datetime.now().strftime("%Y-%m-%d")
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

        import yaml
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
            'at': __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        with open(paper_path, 'w') as f:
            yaml.dump(paper, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        json_response(self, {"ok": True, "status": new_status})

    def log_message(self, format, *args):
        # Quieter logging - suppress API request logs
        try:
            msg = format % args
            if "/api/" in msg:
                return
        except Exception:
            pass
        super().log_message(format, *args)


def main():
    server = HTTPServer(("127.0.0.1", PORT), RetentionHandler)
    print(f"Knowledge Retention Server running at http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
