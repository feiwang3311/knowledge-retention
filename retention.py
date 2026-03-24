#!/usr/bin/env python3
"""
Knowledge Retention Engine
SM-2 spaced repetition + LLM-powered card generation for paper knowledge retention.
"""

import json
import math
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install pyyaml")
    import sys
    sys.exit(1)

# Paths
BASE_DIR = Path(__file__).parent
PAPERS_DIR = BASE_DIR / "papers"
CARDS_DIR = BASE_DIR / "cards"
REVIEW_STATE_FILE = BASE_DIR / "review_state.json"
INTERESTS_FILE = BASE_DIR / "interests.yaml"
FEEDS_FILE = BASE_DIR / "feeds.yaml"


def ensure_dirs():
    CARDS_DIR.mkdir(exist_ok=True)


# ============ File I/O ============

def load_review_state():
    if REVIEW_STATE_FILE.exists():
        with open(REVIEW_STATE_FILE, 'r') as f:
            return json.load(f)
    return {"cards": {}}


def save_review_state(state):
    with open(REVIEW_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_cards(paper_id):
    path = CARDS_DIR / f"{paper_id}.json"
    if path.exists():
        with open(path, 'r') as f:
            return json.load(f)
    return None


def save_cards(paper_id, cards_data):
    ensure_dirs()
    path = CARDS_DIR / f"{paper_id}.json"
    with open(path, 'w') as f:
        json.dump(cards_data, f, indent=2, ensure_ascii=False)


def load_all_cards():
    """Load all card files from cards/ directory."""
    all_cards = {}
    if not CARDS_DIR.exists():
        return all_cards
    for f in CARDS_DIR.glob("*.json"):
        with open(f, 'r') as fh:
            data = json.load(fh)
            all_cards[f.stem] = data
    return all_cards


def load_paper(paper_id):
    path = PAPERS_DIR / f"{paper_id}.yaml"
    if not path.exists():
        return None
    with open(path, 'r') as f:
        data = yaml.safe_load(f) or {}
    data['id'] = paper_id
    return data


def load_all_papers():
    papers = {}
    if not PAPERS_DIR.exists():
        return papers
    for f in PAPERS_DIR.glob("*.yaml"):
        with open(f, 'r') as fh:
            paper = yaml.safe_load(fh) or {}
        paper['id'] = f.stem
        papers[f.stem] = paper
    return papers


def load_interests():
    if INTERESTS_FILE.exists():
        with open(INTERESTS_FILE, 'r') as f:
            return yaml.safe_load(f) or {}
    return {"projects": [], "topics": []}


def load_feeds():
    if FEEDS_FILE.exists():
        with open(FEEDS_FILE, 'r') as f:
            return yaml.safe_load(f) or {}
    return {"arxiv_searches": [], "last_checked": None}


def save_feeds(feeds):
    with open(FEEDS_FILE, 'w') as f:
        yaml.dump(feeds, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ============ SM-2 Algorithm ============

class SM2:
    """Standard SM-2 spaced repetition algorithm."""

    @staticmethod
    def initial_state(card_id, paper_id):
        return {
            "paper_id": paper_id,
            "ease_factor": 2.5,
            "interval_days": 0,
            "repetitions": 0,
            "next_review": datetime.now().strftime("%Y-%m-%d"),
            "last_review": None,
            "history": []
        }

    @staticmethod
    def schedule(card_state, quality):
        """
        Update card state after review.
        quality: 0-5 (mapped from UI: Forgot=1, Hard=3, Good=4, Easy=5)
        Returns updated card_state.
        """
        ef = card_state["ease_factor"]
        interval = card_state["interval_days"]
        reps = card_state["repetitions"]
        today = datetime.now().strftime("%Y-%m-%d")

        # Update ease factor (always, even on fail)
        new_ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        new_ef = max(1.3, new_ef)

        if quality >= 3:  # Pass
            reps += 1
            if reps == 1:
                new_interval = 1
            elif reps == 2:
                new_interval = 6
            else:
                new_interval = round(interval * new_ef)
        else:  # Fail - restart
            reps = 0
            new_interval = 1

        next_review = (datetime.now() + timedelta(days=new_interval)).strftime("%Y-%m-%d")

        card_state["ease_factor"] = round(new_ef, 2)
        card_state["interval_days"] = new_interval
        card_state["repetitions"] = reps
        card_state["next_review"] = next_review
        card_state["last_review"] = today
        card_state["history"].append({
            "date": today,
            "quality": quality,
            "interval_after": new_interval
        })

        return card_state

    @staticmethod
    def get_due_cards(review_state, date=None):
        """Get list of card IDs due for review on date (default: today)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        due = []
        for card_id, state in review_state.get("cards", {}).items():
            if state["next_review"] <= date:
                due.append(card_id)
        return due

    @staticmethod
    def get_stats(review_state):
        """Get review statistics."""
        today = datetime.now().strftime("%Y-%m-%d")
        week_from_now = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        cards = review_state.get("cards", {})
        total = len(cards)
        due_today = sum(1 for s in cards.values() if s["next_review"] <= today)
        upcoming_7d = sum(1 for s in cards.values()
                         if today < s["next_review"] <= week_from_now)
        mastered = sum(1 for s in cards.values() if s["interval_days"] >= 30)
        new_cards = sum(1 for s in cards.values() if s["repetitions"] == 0)

        return {
            "total": total,
            "due_today": due_today,
            "upcoming_7d": upcoming_7d,
            "mastered": mastered,
            "new_cards": new_cards,
            "learning": total - mastered - new_cards
        }


# ============ Card Generator ============

CARD_GENERATION_PROMPT = """You are generating spaced repetition flashcards for a research paper. Generate 5-8 cards that test understanding, not just memorization.

Paper:
Title: {title}
Authors: {authors}
Abstract: {abstract}
Summary: {summary}
Tags: {tags}

{related_context}

Generate cards in these categories:
1. Core Concept (2-3 cards): Test understanding of the paper's key contributions
2. Comparison (1-2 cards): How does this relate to or differ from existing work?
3. Application (1 card): When/where would you use this approach?
4. Limitation (1 card): What are the limitations or open questions?
{connection_instruction}

Output ONLY a JSON array of cards. Each card has:
- "id": short kebab-case identifier (e.g., "paper-slug-01")
- "type": one of "concept", "comparison", "application", "limitation", "connection"
- "question": a question that requires understanding to answer (not just recall)
- "answer": a concise but complete answer (2-4 sentences)
- "difficulty": 1-5 (1=basic, 5=expert)

Output the JSON array only, no other text. Example format:
[
  {{"id": "example-01", "type": "concept", "question": "What is X?", "answer": "X is...", "difficulty": 2}}
]"""


class CardGenerator:
    """Generate knowledge cards from paper metadata using LLM."""

    @staticmethod
    def _build_prompt(paper, related_papers=None):
        related_context = ""
        connection_instruction = ""
        if related_papers:
            titles = [f"- {p.get('title', 'Unknown')}" for p in related_papers[:5]]
            related_context = f"Related papers in the library:\n" + "\n".join(titles)
            connection_instruction = "5. Connection (1 card): How does this paper relate to one of the related papers listed above?"

        return CARD_GENERATION_PROMPT.format(
            title=paper.get('title', 'Unknown'),
            authors=', '.join(paper.get('authors', [])),
            abstract=paper.get('abstract', 'Not available'),
            summary=paper.get('summary', 'Not available'),
            tags=', '.join(paper.get('tags', [])),
            related_context=related_context,
            connection_instruction=connection_instruction,
        )

    @staticmethod
    def _parse_response(text):
        """Extract JSON array from LLM response."""
        # Try to find JSON array in response
        text = text.strip()
        # Remove markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

        try:
            cards = json.loads(text)
            if isinstance(cards, list):
                return cards
        except json.JSONDecodeError:
            pass

        # Try to find array within text
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                cards = json.loads(match.group())
                if isinstance(cards, list):
                    return cards
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def generate_via_claude_cli(paper, related_papers=None):
        """Generate cards using claude CLI (Claude Code subscription)."""
        prompt = CardGenerator._build_prompt(paper, related_papers)

        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                print(f"claude CLI error: {result.stderr}")
                return None
            return CardGenerator._parse_response(result.stdout)
        except FileNotFoundError:
            print("claude CLI not found. Install Claude Code or use ANTHROPIC_API_KEY.")
            return None
        except subprocess.TimeoutExpired:
            print("claude CLI timed out (120s).")
            return None

    @staticmethod
    def generate_via_api(paper, related_papers=None):
        """Generate cards using Anthropic API directly."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        prompt = CardGenerator._build_prompt(paper, related_papers)

        request_body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        }).encode('utf-8')

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=request_body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode('utf-8'))
                text = result["content"][0]["text"]
                return CardGenerator._parse_response(text)
        except Exception as e:
            print(f"API error: {e}")
            return None

    @staticmethod
    def generate(paper, related_papers=None):
        """Generate cards, trying claude CLI first, then API fallback."""
        print(f"Generating cards for: {paper.get('title', 'Unknown')}")

        # Try claude CLI first (uses subscription)
        cards = CardGenerator.generate_via_claude_cli(paper, related_papers)

        # Fallback to API
        if cards is None:
            print("Trying Anthropic API fallback...")
            cards = CardGenerator.generate_via_api(paper, related_papers)

        if cards is None:
            print("Failed to generate cards with any provider.")
            return None

        # Build paper slug for card IDs
        paper_id = paper.get('id', 'unknown')
        slug = paper_id[:30]

        # Ensure card IDs are unique and prefixed
        for i, card in enumerate(cards):
            if not card.get('id', '').startswith(slug[:10]):
                card['id'] = f"{slug}-{i+1:02d}"
            # Validate required fields
            card.setdefault('type', 'concept')
            card.setdefault('difficulty', 2)

        cards_data = {
            "paper_id": paper_id,
            "generated_at": datetime.now().isoformat(),
            "cards": cards
        }

        return cards_data


# ============ Relevance Scoring ============

class RelevanceScorer:
    """Score papers against user interests using keyword matching."""

    @staticmethod
    def score(paper, interests=None):
        if interests is None:
            interests = load_interests()

        # Build searchable text from paper
        text = ' '.join([
            paper.get('title', ''),
            paper.get('abstract', '') or '',
            paper.get('summary', '') or '',
            ' '.join(paper.get('tags', [])),
            ' '.join(paper.get('categories', [])),
        ]).lower()

        if not text.strip():
            return 0.0

        max_score = 0.0

        # Score against projects
        for project in interests.get('projects', []):
            keywords = project.get('keywords', [])
            weight = project.get('weight', 1.0)
            if not keywords:
                continue
            matches = sum(1 for kw in keywords if kw.lower() in text)
            project_score = (matches / len(keywords)) * weight
            max_score = max(max_score, project_score)

        # Score against standalone topics
        topics = interests.get('topics', [])
        if topics:
            topic_matches = sum(1 for t in topics if t.lower() in text)
            topic_score = topic_matches / len(topics) * 0.7  # topics slightly less weight
            max_score = max(max_score, topic_score)

        return round(min(1.0, max_score), 2)


# ============ Connection Finder ============

class ConnectionFinder:
    """Find cross-paper connections via shared tags, categories, and card content."""

    @staticmethod
    def find_connections(paper_id, all_papers=None, all_cards=None):
        if all_papers is None:
            all_papers = load_all_papers()
        if all_cards is None:
            all_cards = load_all_cards()

        if paper_id not in all_papers:
            return []

        paper = all_papers[paper_id]
        paper_tags = set(paper.get('tags', []))
        paper_cats = set(paper.get('categories', []))
        paper_authors = set(paper.get('authors', []))

        connections = []

        for pid, p in all_papers.items():
            if pid == paper_id:
                continue

            reasons = []
            strength = 0

            # Shared authors
            shared_authors = paper_authors & set(p.get('authors', []))
            if shared_authors:
                reasons.append(f"shared authors: {', '.join(shared_authors)}")
                strength += 3

            # Shared tags
            shared_tags = paper_tags & set(p.get('tags', []))
            if len(shared_tags) >= 2:
                reasons.append(f"shared tags: {', '.join(shared_tags)}")
                strength += len(shared_tags)

            # Shared categories
            shared_cats = paper_cats & set(p.get('categories', []))
            if shared_cats:
                reasons.append(f"shared categories: {', '.join(shared_cats)}")
                strength += 2

            if reasons:
                connections.append({
                    "paper_id": pid,
                    "title": p.get('title', 'Unknown'),
                    "reasons": reasons,
                    "strength": strength
                })

        # Sort by strength
        connections.sort(key=lambda c: c["strength"], reverse=True)
        return connections


# ============ Daily Feed Checker ============

def fetch_arxiv_search(query, categories=None, max_results=5):
    """Search arXiv API for papers matching query."""
    import xml.etree.ElementTree as ET

    search_query = f"all:{query}"
    if categories:
        cat_query = "+OR+".join(f"cat:{c}" for c in categories)
        search_query = f"({search_query})+AND+({cat_query})"

    url = (
        f"http://export.arxiv.org/api/query?"
        f"search_query={search_query.replace(' ', '+')}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={max_results}"
    )

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            xml_text = response.read().decode('utf-8')

        root = ET.fromstring(xml_text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        papers = []
        for entry in root.findall('atom:entry', ns):
            title = entry.find('atom:title', ns)
            summary = entry.find('atom:summary', ns)
            published = entry.find('atom:published', ns)
            link = entry.find("atom:id", ns)

            authors = []
            for author in entry.findall('atom:author', ns):
                name = author.find('atom:name', ns)
                if name is not None:
                    authors.append(name.text.strip())

            arxiv_id_match = re.search(r'(\d+\.\d+)', link.text if link is not None else '')
            arxiv_id = arxiv_id_match.group(1) if arxiv_id_match else None

            year = None
            if published is not None and published.text:
                year_match = re.match(r'(\d{4})', published.text)
                year = int(year_match.group(1)) if year_match else None

            papers.append({
                'title': title.text.strip().replace('\n', ' ') if title is not None else 'Unknown',
                'authors': authors,
                'year': year,
                'abstract': summary.text.strip().replace('\n', ' ') if summary is not None else '',
                'url': f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else '',
                'arxiv_id': arxiv_id,
            })

        return papers
    except Exception as e:
        print(f"arXiv search error: {e}")
        return []


def run_daily_check():
    """Run daily feed check: fetch new papers, score relevance, add discoveries."""
    feeds = load_feeds()
    interests = load_interests()
    existing_papers = load_all_papers()

    # Track existing arXiv IDs to avoid duplicates
    existing_urls = {p.get('url', '') for p in existing_papers.values()}

    new_discoveries = []

    for search in feeds.get('arxiv_searches', []):
        query = search.get('query', '')
        categories = search.get('categories', [])
        max_results = search.get('max_results', 5)

        if not query:
            continue

        print(f"Searching arXiv: {query}")
        results = fetch_arxiv_search(query, categories, max_results)

        for paper in results:
            if paper.get('url') in existing_urls:
                continue

            # Score relevance
            score = RelevanceScorer.score(paper, interests)
            paper['relevance_score'] = score

            if score >= 0.3:  # Threshold for auto-add
                new_discoveries.append(paper)
                existing_urls.add(paper.get('url', ''))

    # Save new discoveries
    for paper in new_discoveries:
        from papers_cli import get_paper_id, save_yaml, PAPERS_DIR, ensure_dirs as ensure_paper_dirs
        ensure_paper_dirs()

        paper_id = get_paper_id(paper['title'], paper.get('year', 2025))
        paper_path = PAPERS_DIR / f"{paper_id}.yaml"

        if paper_path.exists():
            continue

        paper_data = {
            'title': paper['title'],
            'authors': paper['authors'],
            'year': paper.get('year'),
            'abstract': paper.get('abstract'),
            'summary': paper.get('abstract'),
            'url': paper.get('url'),
            'tags': [],
            'categories': [],
            'status': 'discovered',
            'relevance_score': paper.get('relevance_score', 0),
            'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        paper_data = {k: v for k, v in paper_data.items() if v is not None}
        save_yaml(paper_path, paper_data)
        print(f"  + [{paper['relevance_score']:.1f}] {paper['title'][:60]}")

    # Update last checked
    feeds['last_checked'] = datetime.now().isoformat()
    save_feeds(feeds)

    # Print summary
    state = load_review_state()
    stats = SM2.get_stats(state)
    print(f"\n--- Daily Summary ---")
    print(f"New discoveries: {len(new_discoveries)}")
    print(f"Cards due today: {stats['due_today']}")
    print(f"Total cards: {stats['total']} (mastered: {stats['mastered']})")

    return new_discoveries


# ============ Register Cards in Review State ============

def register_cards(cards_data):
    """Add new cards to review_state.json with initial SM-2 state."""
    state = load_review_state()
    paper_id = cards_data["paper_id"]

    for card in cards_data["cards"]:
        card_id = card["id"]
        if card_id not in state["cards"]:
            state["cards"][card_id] = SM2.initial_state(card_id, paper_id)

    save_review_state(state)
    return len(cards_data["cards"])
