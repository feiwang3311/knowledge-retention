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
        try:
            with open(REVIEW_STATE_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "cards" in data:
                    return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: corrupted review_state.json: {e}")
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
        try:
            with open(f, 'r') as fh:
                data = json.load(fh)
            all_cards[f.stem] = data
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: skipping corrupted card file {f.name}: {e}")
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

        if quality >= 3:  # Pass
            reps += 1
            if reps == 1:
                new_interval = 1
            elif reps == 2:
                new_interval = 6
            else:
                new_interval = round(interval * ef)  # Use OLD ef per SM-2 spec
            new_interval = min(new_interval, 365)  # Cap at 1 year
        else:  # Fail - restart
            reps = 0
            new_interval = 1

        # Update ease factor AFTER interval calculation (SM-2 spec)
        new_ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        new_ef = max(1.3, new_ef)

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

IMPORTANT STYLE RULES:
- Explain concepts using plain language, analogies, and concrete examples — as if explaining to a smart engineer who is NOT a specialist in this exact subfield
- Avoid raw math notation in answers. Instead, describe the intuition behind mathematical ideas. For example, instead of "minimize ||Ax - b||²", say "find the closest approximation to the target by minimizing the gap between prediction and reality"
- Use everyday analogies when possible. For example, "polyhedral transformations rearrange loop computations like reorganizing a warehouse — you change the order items are picked to minimize walking distance, without changing what gets shipped"
- Keep answers concrete: what does it DO, why does it MATTER, when would you USE it

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
- "answer": a concise but complete answer (2-4 sentences, plain language, use analogies)
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

        # Validate and clean card structure from LLM
        valid_cards = []
        for i, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            # Require question and answer
            if not card.get('question') or not card.get('answer'):
                continue
            if not card.get('id', '').startswith(slug[:10]):
                card['id'] = f"{slug}-{i+1:02d}"
            card.setdefault('type', 'concept')
            card.setdefault('difficulty', 2)
            valid_cards.append(card)

        if not valid_cards:
            print("LLM returned no valid cards.")
            return None

        cards_data = {
            "paper_id": paper_id,
            "generated_at": datetime.now().isoformat(),
            "cards": valid_cards
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


def _strip_html(text):
    """Remove HTML tags from text."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch_rss_feed(feed_url, max_results=10):
    """Fetch items from an RSS/Atom feed."""
    import xml.etree.ElementTree as ET

    try:
        req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            xml_text = response.read().decode('utf-8')

        root = ET.fromstring(xml_text)
        items = []

        # Try RSS 2.0 format
        for item in root.findall('.//item')[:max_results]:
            title = item.find('title')
            link = item.find('link')
            desc = item.find('description')
            desc_text = _strip_html(desc.text)[:500] if desc is not None and desc.text else ''
            items.append({
                'title': title.text.strip() if title is not None and title.text else 'Unknown',
                'url': link.text.strip() if link is not None and link.text else '',
                'abstract': desc_text,
                'authors': [],
                'year': datetime.now().year,
                'source_type': 'rss',
            })

        # Try Atom format if no RSS items found
        if not items:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('atom:entry', ns)[:max_results]:
                title = entry.find('atom:title', ns)
                link = entry.find('atom:link', ns)
                summary = entry.find('atom:summary', ns) or entry.find('atom:content', ns)
                link_href = link.get('href', '') if link is not None else ''

                authors = []
                for author in entry.findall('atom:author', ns):
                    name = author.find('atom:name', ns)
                    if name is not None and name.text:
                        authors.append(name.text.strip())

                items.append({
                    'title': title.text.strip() if title is not None and title.text else 'Unknown',
                    'url': link_href,
                    'abstract': summary.text.strip()[:500] if summary is not None and summary.text else '',
                    'authors': authors,
                    'year': datetime.now().year,
                    'source_type': 'rss',
                })

        return items
    except Exception as e:
        print(f"RSS feed error ({feed_url}): {e}")
        return []


def scrape_web_page(url):
    """Extract title and text content from any web page for card generation."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            html_content = response.read().decode('utf-8', errors='replace')

        # Extract title
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else 'Unknown'

        # Extract og:description or meta description
        desc_match = re.search(
            r'<meta[^>]*(?:name="description"|property="og:description")[^>]*content="([^"]*)"',
            html_content, re.IGNORECASE
        )
        description = desc_match.group(1).strip() if desc_match else ''

        # Extract main text (strip tags, collapse whitespace)
        # Remove script/style blocks
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Limit to first ~2000 chars for summary
        text = text[:2000]

        return {
            'title': title,
            'description': description,
            'text': text,
        }
    except Exception as e:
        print(f"Scrape error ({url}): {e}")
        return None


def run_daily_check():
    """Run daily feed check: fetch new papers from arXiv + RSS, score relevance, add discoveries."""
    feeds = load_feeds()
    interests = load_interests()
    existing_papers = load_all_papers()

    # Track existing URLs to avoid duplicates
    existing_urls = {p.get('url', '') for p in existing_papers.values()}

    new_discoveries = []

    # 1. arXiv searches
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
            score = RelevanceScorer.score(paper, interests)
            paper['relevance_score'] = score
            if score >= 0.3:
                paper['source_type'] = 'arxiv'
                new_discoveries.append(paper)
                existing_urls.add(paper.get('url', ''))

    # 2. RSS feeds
    for feed in feeds.get('rss_feeds', []):
        feed_url = feed.get('url', '')
        feed_name = feed.get('name', feed_url)
        max_results = feed.get('max_results', 10)

        if not feed_url:
            continue

        print(f"Fetching RSS: {feed_name}")
        results = fetch_rss_feed(feed_url, max_results)

        for item in results:
            if item.get('url') in existing_urls:
                continue
            score = RelevanceScorer.score(item, interests)
            item['relevance_score'] = score
            if score >= 0.3:
                item['source'] = feed_name
                new_discoveries.append(item)
                existing_urls.add(item.get('url', ''))

    # Save new discoveries
    for paper in new_discoveries:
        from papers_cli import get_paper_id, save_yaml, PAPERS_DIR, ensure_dirs as ensure_paper_dirs
        ensure_paper_dirs()

        paper_id = get_paper_id(paper['title'], paper.get('year', datetime.now().year))
        paper_path = PAPERS_DIR / f"{paper_id}.yaml"

        if paper_path.exists():
            continue

        paper_data = {
            'title': paper['title'],
            'authors': paper.get('authors', []),
            'year': paper.get('year'),
            'abstract': paper.get('abstract', ''),
            'summary': paper.get('abstract', ''),
            'url': paper.get('url'),
            'tags': [],
            'categories': [],
            'status': 'discovered',
            'source_type': paper.get('source_type', 'unknown'),
            'source': paper.get('source', ''),
            'relevance_score': paper.get('relevance_score', 0),
            'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        paper_data = {k: v for k, v in paper_data.items() if v is not None and v != '' and v != []}
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


# ============ Disk Management ============

# Max disk usage for PDFs in MB. Papers beyond this won't auto-download PDFs.
PDF_BUDGET_MB = 500
# Max number of "discovered" papers to keep (oldest get purged)
MAX_DISCOVERED = 200


def get_disk_usage():
    """Get disk usage breakdown in bytes."""
    usage = {"papers": 0, "cards": 0, "pdfs": 0, "total": 0}

    for d, key in [(PAPERS_DIR, "papers"), (CARDS_DIR, "cards"), (BASE_DIR / "pdfs", "pdfs")]:
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    usage[key] += f.stat().st_size

    usage["total"] = sum(usage.values())
    return usage


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def cleanup_old_discoveries(max_keep=MAX_DISCOVERED):
    """Remove oldest 'discovered' papers that have no cards and no status change.
    These are auto-fetched papers that were never promoted or engaged with."""
    import yaml

    papers = load_all_papers()
    all_cards = load_all_cards()

    # Find discovered papers with no cards
    discovered = []
    for pid, p in papers.items():
        if p.get('status') == 'discovered' and pid not in all_cards:
            added = p.get('added_at', '1970-01-01')
            discovered.append((pid, added))

    # Sort oldest first
    discovered.sort(key=lambda x: x[1])

    # Remove excess
    to_remove = discovered[:-max_keep] if len(discovered) > max_keep else []
    removed = 0

    for pid, _ in to_remove:
        paper_path = PAPERS_DIR / f"{pid}.yaml"
        if paper_path.exists():
            paper_path.unlink()
            removed += 1

    return removed


def cleanup_pdfs_for_mastered():
    """Remove PDFs for papers that are mastered (all cards interval > 30d).
    The metadata and cards are kept — only the large PDF file is removed."""
    import yaml

    papers = load_all_papers()
    state = load_review_state()
    removed = 0
    freed = 0

    for pid, paper in papers.items():
        pdf_path_str = paper.get('pdf_path')
        if not pdf_path_str:
            continue

        pdf_full = BASE_DIR / pdf_path_str
        if not pdf_full.exists():
            continue

        # Check if all cards for this paper are mastered (interval >= 30d)
        paper_cards = [cid for cid, cs in state.get("cards", {}).items()
                       if cs["paper_id"] == pid]

        if not paper_cards:
            continue

        all_mastered = all(
            state["cards"][cid]["interval_days"] >= 30
            for cid in paper_cards
        )

        if all_mastered:
            size = pdf_full.stat().st_size
            pdf_full.unlink()
            freed += size
            removed += 1

            # Update paper YAML to remove pdf_path
            paper_path = PAPERS_DIR / f"{pid}.yaml"
            with open(paper_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            data.pop('pdf_path', None)
            with open(paper_path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return removed, freed


def run_cleanup():
    """Full cleanup: purge old discoveries + free PDF space."""
    print("=== Disk Cleanup ===\n")

    usage = get_disk_usage()
    print(f"Current usage: papers={format_size(usage['papers'])}, "
          f"cards={format_size(usage['cards'])}, "
          f"pdfs={format_size(usage['pdfs'])}, "
          f"total={format_size(usage['total'])}")

    # Count papers
    papers = load_all_papers()
    status_counts = {}
    for p in papers.values():
        s = p.get('status', 'unknown')
        status_counts[s] = status_counts.get(s, 0) + 1
    print(f"Papers: {len(papers)} total — {status_counts}")

    # Cleanup old discoveries
    removed_disc = cleanup_old_discoveries()
    if removed_disc:
        print(f"\nRemoved {removed_disc} old discovered papers (no cards, exceeded {MAX_DISCOVERED} limit)")

    # Cleanup mastered PDFs
    removed_pdfs, freed = cleanup_pdfs_for_mastered()
    if removed_pdfs:
        print(f"Removed {removed_pdfs} PDFs for mastered papers (freed {format_size(freed)})")

    if not removed_disc and not removed_pdfs:
        print("\nNothing to clean up.")

    # Final usage
    usage = get_disk_usage()
    print(f"\nFinal usage: {format_size(usage['total'])}")
