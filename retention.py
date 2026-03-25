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


STUDIED_STATUSES = {'read', 'reviewing', 'mastered'}


def get_studied_paper_ids(papers=None):
    """Get set of paper IDs that have been studied (status is read/reviewing/mastered)."""
    if papers is None:
        papers = load_all_papers()
    return {pid for pid, p in papers.items() if p.get('status') in STUDIED_STATUSES}


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
    def get_due_cards(review_state, date=None, studied_paper_ids=None):
        """Get list of card IDs due for review on date (default: today).
        If studied_paper_ids is provided, only include cards from those papers
        (papers the user has actually studied)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        due = []
        for card_id, state in review_state.get("cards", {}).items():
            if state["next_review"] <= date:
                if studied_paper_ids is not None:
                    if state.get("paper_id") not in studied_paper_ids:
                        continue
                due.append(card_id)
        return due

    @staticmethod
    def get_stats(review_state, studied_paper_ids=None):
        """Get review statistics. If studied_paper_ids given, only count those."""
        today = datetime.now().strftime("%Y-%m-%d")
        week_from_now = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        cards = review_state.get("cards", {})
        if studied_paper_ids is not None:
            cards = {k: v for k, v in cards.items() if v.get("paper_id") in studied_paper_ids}
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
        # Use a threshold-based approach: 1 match = 0.2, 2 = 0.4, 3+ = 0.6+
        for project in interests.get('projects', []):
            keywords = project.get('keywords', [])
            weight = project.get('weight', 1.0)
            if not keywords:
                continue
            matches = sum(1 for kw in keywords if kw.lower() in text)
            if matches == 0:
                continue
            # Diminishing returns: first matches count more
            project_score = min(1.0, 0.2 * matches) * weight
            max_score = max(max_score, project_score)

        # Score against standalone topics — any single topic match = 0.3
        topics = interests.get('topics', [])
        if topics:
            topic_matches = sum(1 for t in topics if t.lower() in text)
            if topic_matches > 0:
                topic_score = min(1.0, 0.3 * topic_matches)
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


# ============ Semantic Scholar Discovery ============

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_REC_BASE = "https://api.semanticscholar.org/recommendations/v1"
S2_FIELDS = "title,abstract,year,citationCount,authors,url,externalIds,tldr"
S2_API_KEY = os.environ.get("S2_API_KEY")  # Optional but recommended


def _s2_get(url, timeout=15):
    """Make a GET request to Semantic Scholar API."""
    import time
    req = urllib.request.Request(url, headers={'User-Agent': 'KnowledgeRetention/1.0'})
    if S2_API_KEY:
        req.add_header("x-api-key", S2_API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("  Rate limited by Semantic Scholar, waiting 3s...")
            time.sleep(3)
            try:
                req2 = urllib.request.Request(url, headers={'User-Agent': 'KnowledgeRetention/1.0'})
                if S2_API_KEY:
                    req2.add_header("x-api-key", S2_API_KEY)
                with urllib.request.urlopen(req2, timeout=timeout) as response:
                    return json.loads(response.read().decode('utf-8'))
            except Exception:
                pass
        else:
            print(f"  S2 API HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        print(f"  S2 API error: {e}")
        return None


def _s2_post(url, body, timeout=15):
    """Make a POST request to Semantic Scholar API."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'KnowledgeRetention/1.0',
        },
        method='POST'
    )
    if S2_API_KEY:
        req.add_header("x-api-key", S2_API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"  S2 API error: {e}")
        return None


def _s2_paper_to_dict(p):
    """Convert S2 API paper object to our internal format."""
    if not p or not p.get('title'):
        return None
    authors = [a.get('name', '') for a in (p.get('authors') or []) if a.get('name')]
    ext_ids = p.get('externalIds') or {}
    arxiv_id = ext_ids.get('ArXiv')
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (p.get('url') or '')
    tldr = p.get('tldr', {})
    summary = tldr.get('text', '') if tldr else ''

    return {
        'title': p['title'],
        'authors': authors,
        'year': p.get('year') or datetime.now().year,
        'abstract': p.get('abstract') or summary or '',
        'summary': summary or (p.get('abstract') or '')[:300],
        'url': url,
        'citation_count': p.get('citationCount') or 0,
        's2_id': p.get('paperId', ''),
        'source_type': 'semantic_scholar',
    }


def s2_semantic_search(query, year_from=None, min_citations=0, limit=10):
    """Search Semantic Scholar by meaning (not just keywords)."""
    from urllib.parse import urlencode, quote
    params = {
        'query': query,
        'fields': S2_FIELDS,
        'limit': min(limit, 100),
        'fieldsOfStudy': 'Computer Science',
    }
    if year_from:
        params['year'] = f"{year_from}-"
    if min_citations:
        params['minCitationCount'] = str(min_citations)

    url = f"{S2_BASE}/paper/search?{urlencode(params, quote_via=quote)}"
    result = _s2_get(url)
    if not result or 'data' not in result:
        return []

    papers = []
    for p in result['data']:
        paper = _s2_paper_to_dict(p)
        if paper:
            papers.append(paper)
    return papers


def s2_get_recommendations(seed_paper_ids, limit=20):
    """Get paper recommendations based on seed papers.
    seed_paper_ids can be S2 IDs or 'ARXIV:xxxx.xxxxx' format."""
    url = f"{S2_REC_BASE}/papers/?fields={S2_FIELDS}&limit={limit}"
    body = {
        "positivePaperIds": seed_paper_ids,
        "negativePaperIds": [],
    }
    result = _s2_post(url, body)
    if not result or 'recommendedPapers' not in result:
        return []

    papers = []
    for p in result['recommendedPapers']:
        paper = _s2_paper_to_dict(p)
        if paper:
            papers.append(paper)
    return papers


def s2_get_citations(paper_id, limit=20):
    """Get papers that cite a given paper (recent, important follow-up work)."""
    from urllib.parse import urlencode
    url = f"{S2_BASE}/paper/{paper_id}/citations?{urlencode({'fields': S2_FIELDS, 'limit': limit})}"
    result = _s2_get(url)
    if not result or 'data' not in result:
        return []

    papers = []
    for item in result['data']:
        p = item.get('citingPaper', {})
        paper = _s2_paper_to_dict(p)
        if paper:
            papers.append(paper)
    # Sort by citation count (most impactful first)
    papers.sort(key=lambda x: x.get('citation_count', 0), reverse=True)
    return papers


def s2_get_references(paper_id, limit=20):
    """Get papers referenced by a given paper (foundational work)."""
    from urllib.parse import urlencode
    url = f"{S2_BASE}/paper/{paper_id}/references?{urlencode({'fields': S2_FIELDS, 'limit': limit})}"
    result = _s2_get(url)
    if not result or 'data' not in result:
        return []

    papers = []
    for item in result['data']:
        p = item.get('citedPaper', {})
        paper = _s2_paper_to_dict(p)
        if paper:
            papers.append(paper)
    papers.sort(key=lambda x: x.get('citation_count', 0), reverse=True)
    return papers


def discover_via_semantic_scholar(interests=None, existing_papers=None):
    """Smart paper discovery using Semantic Scholar.

    Strategy:
    1. Semantic search for each interest area (finds papers by meaning, not keywords)
    2. Get recommendations based on papers already in the library
    3. Walk citation graph of existing papers (find important follow-ups)

    Returns list of discovered papers, sorted by citation count.
    """
    import time

    if interests is None:
        interests = load_interests()
    if existing_papers is None:
        existing_papers = load_all_papers()

    existing_urls = {p.get('url', '') for p in existing_papers.values()}
    existing_s2_ids = {p.get('s2_id', '') for p in existing_papers.values() if p.get('s2_id')}

    all_discoveries = {}  # url -> paper dict (dedup)
    seen_s2_ids = set(existing_s2_ids)

    def add_paper(paper):
        url = paper.get('url', '')
        s2_id = paper.get('s2_id', '')
        if url and url in existing_urls:
            return
        if s2_id and s2_id in seen_s2_ids:
            return
        if url and url in all_discoveries:
            return
        if url:
            all_discoveries[url] = paper
            if s2_id:
                seen_s2_ids.add(s2_id)

    # 1. Semantic search for each project/topic
    print("  Semantic search...")
    for project in interests.get('projects', []):
        name = project.get('name', '')
        keywords = project.get('keywords', [])
        if not name:
            continue
        # Build a natural language query from project name + top keywords
        query = f"{name} {' '.join(keywords[:3])}"
        print(f"    Searching: {query}")
        papers = s2_semantic_search(query, year_from=2023, limit=10)
        for p in papers:
            p['discovery_reason'] = f"semantic search: {name}"
            add_paper(p)
        time.sleep(1)  # Rate limit

    for topic in interests.get('topics', []):
        print(f"    Searching: {topic}")
        papers = s2_semantic_search(topic, year_from=2023, limit=5)
        for p in papers:
            p['discovery_reason'] = f"semantic search: {topic}"
            add_paper(p)
        time.sleep(1)

    # 2. Recommendations based on existing papers
    seed_ids = []
    for pid, p in existing_papers.items():
        # Use arXiv ID if available
        url = p.get('url', '')
        arxiv_match = re.search(r'arxiv\.org/abs/(\d+\.\d+)', url)
        if arxiv_match:
            seed_ids.append(f"ARXIV:{arxiv_match.group(1)}")
        elif p.get('s2_id'):
            seed_ids.append(p['s2_id'])

    if seed_ids:
        print(f"  Getting recommendations from {len(seed_ids)} seed papers...")
        # Use up to 5 seeds (API limit consideration)
        recs = s2_get_recommendations(seed_ids[:5], limit=20)
        for p in recs:
            p['discovery_reason'] = "recommended based on your library"
            add_paper(p)
        time.sleep(1)

    # 3. Citation graph: find impactful papers citing our top seeds
    papers_by_cites = sorted(existing_papers.items(),
                              key=lambda x: x[1].get('citation_count') or 0, reverse=True)
    for pid, p in papers_by_cites[:3]:  # Top 3 by citation count
        url = p.get('url', '')
        arxiv_match = re.search(r'arxiv\.org/abs/(\d+\.\d+)', url)
        if arxiv_match:
            s2_id = f"ARXIV:{arxiv_match.group(1)}"
        elif p.get('s2_id'):
            s2_id = p['s2_id']
        else:
            continue

        print(f"  Citation walk: {p.get('title', '?')[:40]}...")
        citations = s2_get_citations(s2_id, limit=10)
        for c in citations:
            c['discovery_reason'] = f"cites: {p.get('title', '?')[:40]}"
            add_paper(c)
        time.sleep(1)

    # Sort all discoveries by citation count (most important first)
    results = sorted(all_discoveries.values(),
                     key=lambda x: x.get('citation_count', 0), reverse=True)

    print(f"  Found {len(results)} unique papers via Semantic Scholar")
    return results


def generate_seed_papers_prompt(interests):
    """Build a prompt for the LLM to suggest seminal papers for each interest area."""
    projects = interests.get('projects', [])
    topics = interests.get('topics', [])

    areas = []
    for p in projects:
        name = p.get('name', 'Unknown')
        areas.append(f"- {name} (keywords: {', '.join(p.get('keywords', []))})")
    for t in topics:
        areas.append(f"- {t}")

    return f"""I'm building a knowledge base for these research areas:

{chr(10).join(areas)}

For each area, suggest the 5 most important/seminal papers I should read.
Prioritize:
1. Foundational papers that everyone in the field should know
2. Recent breakthrough papers (2023-2025) that changed the direction
3. Good survey papers that give broad understanding

Output as a JSON array of objects, each with:
- "title": exact paper title
- "area": which research area it belongs to
- "why": one sentence on why this paper matters
- "year": publication year

Output ONLY the JSON array, no other text."""


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
            if score >= 0.2:
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
            if score >= 0.2:
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


# ============ Explorations ============

EXPLORATIONS_FILE = BASE_DIR / "explorations.json"

def load_explorations():
    if EXPLORATIONS_FILE.exists():
        try:
            with open(EXPLORATIONS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"explorations": []}

def save_explorations(data):
    with open(EXPLORATIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def answer_question(question, exploration_context=None):
    """Answer a question using the paper library + Semantic Scholar + LLM.

    Returns dict with:
    - answer: string
    - papers_used: list of paper_ids from library that were used as context
    - papers_discovered: list of new papers found and added (as dicts with id, title, url)
    - follow_ups: list of suggested follow-up questions
    - cards: list of generated cards (question/answer dicts)
    """
    import time

    papers = load_all_papers()
    all_cards = load_all_cards()

    # 1. Find relevant papers in library by keyword matching
    q_lower = question.lower()
    q_words = set(re.sub(r'[^\w\s]', '', q_lower).split())
    # Remove common words
    stop_words = {'the','a','an','is','are','was','were','how','what','why','which','does','do','and','or','in','on','of','to','for','with','from','by','about','between','this','that','these','those','it','its','can','could','would','should','has','have','had'}
    q_words -= stop_words

    relevant = []
    for pid, p in papers.items():
        searchable = ' '.join([
            p.get('title', ''),
            p.get('abstract', '') or '',
            p.get('summary', '') or '',
            ' '.join(p.get('tags', [])),
        ]).lower()

        matches = sum(1 for w in q_words if w in searchable)
        if matches >= 1:
            relevant.append((pid, p, matches))

    relevant.sort(key=lambda x: x[2], reverse=True)
    relevant_papers = [(pid, p) for pid, p, _ in relevant[:8]]
    papers_used = [pid for pid, _ in relevant_papers]

    # 2. If we have fewer than 3 relevant papers, search Semantic Scholar
    papers_discovered = []
    if len(relevant_papers) < 3:
        try:
            s2_results = s2_semantic_search(question, year_from=2020, limit=5)
            time.sleep(1)

            existing_urls = {p.get('url', '') for p in papers.values()}

            for s2_paper in s2_results[:3]:
                if s2_paper.get('url') in existing_urls:
                    continue
                # Add to library
                from papers_cli import get_paper_id, save_yaml, PAPERS_DIR
                PAPERS_DIR.mkdir(exist_ok=True)
                new_id = get_paper_id(s2_paper['title'], s2_paper.get('year', 2025))
                paper_path = PAPERS_DIR / f"{new_id}.yaml"
                if paper_path.exists():
                    continue

                paper_data = {
                    'title': s2_paper['title'],
                    'authors': s2_paper.get('authors', []),
                    'year': s2_paper.get('year'),
                    'abstract': s2_paper.get('abstract', ''),
                    'summary': s2_paper.get('summary', ''),
                    'url': s2_paper.get('url', ''),
                    'status': 'discovered',
                    'source_type': 'semantic_scholar',
                    'discovery_reason': f'exploration: {question[:50]}',
                    'citation_count': s2_paper.get('citation_count', 0),
                    's2_id': s2_paper.get('s2_id', ''),
                    'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                paper_data = {k: v for k, v in paper_data.items() if v is not None and v != '' and v != []}
                save_yaml(paper_path, paper_data)

                papers_discovered.append({
                    'id': new_id,
                    'title': s2_paper['title'],
                    'url': s2_paper.get('url', ''),
                })

                # Also use as context
                relevant_papers.append((new_id, paper_data))
                papers_used.append(new_id)
                existing_urls.add(s2_paper.get('url', ''))
        except Exception as e:
            print(f"S2 search during exploration failed: {e}")

    # 3. Build context for LLM
    context_parts = []
    for pid, p in relevant_papers[:6]:
        part = f"Paper: {p.get('title', 'Unknown')} ({p.get('year', '?')})\n"
        part += f"Abstract: {p.get('abstract', '') or p.get('summary', '')}\n"

        # Include cards if available
        paper_cards = all_cards.get(pid, {}).get('cards', [])
        if paper_cards:
            part += "Key points:\n"
            for c in paper_cards[:5]:
                part += f"- {c.get('question', '')}: {c.get('answer', '')}\n"
        context_parts.append(part)

    # Add exploration context if provided
    if exploration_context:
        context_parts.append(f"Previous questions in this exploration:\n{exploration_context}")

    context = "\n---\n".join(context_parts)

    prompt = f"""You are a research assistant helping a user understand academic papers. Answer the question using the paper context provided. Use plain language and analogies — the user prefers intuitive explanations over math notation.

CONTEXT FROM USER'S PAPER LIBRARY:
{context}

USER'S QUESTION:
{question}

Provide:
1. A clear, detailed answer (use the paper context, cite papers by name when relevant)
2. After your answer, on a new line write "FOLLOW_UPS:" followed by 3 suggested follow-up questions (one per line, prefixed with "- ")
3. After follow-ups, on a new line write "CARDS:" followed by 2-3 knowledge cards in this format (one per line):
   Q: [question] | A: [answer]

Be thorough but accessible. If the papers don't fully answer the question, say what's missing and what additional reading would help."""

    # 4. Call Claude
    answer_text = ""
    follow_ups = []
    cards = []

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            raw = result.stdout.strip()

            # Parse sections
            answer_text = raw
            follow_ups_text = ""
            cards_text = ""

            if "FOLLOW_UPS:" in raw:
                parts = raw.split("FOLLOW_UPS:", 1)
                answer_text = parts[0].strip()
                remainder = parts[1]
                if "CARDS:" in remainder:
                    fu_part, cards_text = remainder.split("CARDS:", 1)
                    follow_ups_text = fu_part.strip()
                    cards_text = cards_text.strip()
                else:
                    follow_ups_text = remainder.strip()
            elif "CARDS:" in raw:
                answer_text, cards_text = raw.split("CARDS:", 1)
                answer_text = answer_text.strip()
                cards_text = cards_text.strip()

            # Parse follow-ups
            for line in follow_ups_text.split('\n'):
                line = line.strip()
                if line.startswith('- '):
                    follow_ups.append(line[2:].strip())
                elif line and not line.startswith('CARDS'):
                    follow_ups.append(line.strip())
            follow_ups = follow_ups[:5]

            # Parse cards
            for line in cards_text.split('\n'):
                line = line.strip()
                if line.startswith('Q:') and '|' in line and 'A:' in line:
                    q_part, a_part = line.split('|', 1)
                    q = q_part.replace('Q:', '').strip()
                    a = a_part.replace('A:', '').strip()
                    if q and a:
                        cards.append({"question": q, "answer": a, "type": "exploration"})
        else:
            answer_text = f"Failed to generate answer: {result.stderr[:200]}"
    except Exception as e:
        answer_text = f"Error generating answer: {str(e)}"

    return {
        "answer": answer_text,
        "papers_used": papers_used,
        "papers_discovered": papers_discovered,
        "follow_ups": follow_ups,
        "cards": cards,
    }


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
