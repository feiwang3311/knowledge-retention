#!/usr/bin/env python3
"""
Daily automation script for Knowledge Retention.
Run via launchd or cron. Does everything automatically:
1. Fetch new papers from arXiv feeds
2. Auto-generate cards for high-relevance papers
3. Send macOS notification with review summary
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from retention import (
    SM2, CardGenerator, RelevanceScorer,
    load_review_state, load_all_papers, load_all_cards,
    load_paper, load_interests, save_cards, register_cards,
    run_daily_check, discover_via_semantic_scholar,
    cleanup_old_discoveries, cleanup_pdfs_for_mastered,
    get_disk_usage, format_size,
)
from papers_cli import get_paper_id, save_yaml, PAPERS_DIR, ensure_dirs

LOG_FILE = Path(__file__).parent / "daily.log"
AUTO_GENERATE_THRESHOLD = 0.3  # Generate cards for papers with relevance >= this
DAILY_CARD_LIMIT = 3  # Max papers to generate cards for per day (pacing)


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def notify(title, message):
    """Send macOS notification."""
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"'
        ], timeout=10)
    except Exception as e:
        log(f"Notification failed: {e}")


def auto_generate_cards():
    """Generate cards for top papers without cards. Paced to DAILY_CARD_LIMIT per day."""
    papers = load_all_papers()
    all_cards = load_all_cards()
    generated = 0

    # Build candidates: papers without cards, sorted by priority
    candidates = []
    for pid, paper in papers.items():
        if pid in all_cards:
            continue
        status = paper.get('status', 'unread')
        relevance = paper.get('relevance_score', 0)

        # Manually added papers get high priority
        is_manual = status in ('unread', 'reading', 'read', 'queued')
        is_high_relevance = relevance >= AUTO_GENERATE_THRESHOLD

        if not (is_manual or is_high_relevance):
            continue

        # Priority: manual papers first, then by relevance
        priority = (1 if is_manual else 0, relevance)
        candidates.append((pid, paper, priority))

    # Sort: manual first, then highest relevance
    candidates.sort(key=lambda x: x[2], reverse=True)
    # Limit to daily cap
    candidates = candidates[:DAILY_CARD_LIMIT]

    for pid, paper, _ in candidates:

        log(f"Auto-generating cards for: {paper.get('title', pid)[:60]}")
        related = [p for ppid, p in papers.items() if ppid != pid]

        try:
            cards_data = CardGenerator.generate(paper, related)
            if cards_data:
                save_cards(pid, cards_data)
                count = register_cards(cards_data)
                log(f"  Generated {count} cards")
                generated += 1
        except Exception as e:
            log(f"  Card generation failed: {e}")

    return generated


def main():
    log("=== Daily automation started ===")

    # 1a. Fetch new papers from RSS/arXiv
    log("Step 1a: Checking arXiv/RSS feeds...")
    try:
        new_papers = run_daily_check()
        log(f"Found {len(new_papers)} papers from feeds")
    except Exception as e:
        log(f"Feed check failed: {e}")
        new_papers = []

    # 1b. Smart discovery via Semantic Scholar
    log("Step 1b: Semantic Scholar discovery...")
    try:
        interests = load_interests()
        existing = load_all_papers()
        existing_urls = {p.get('url', '') for p in existing.values()}

        s2_papers = discover_via_semantic_scholar(interests, existing)
        s2_added = 0
        ensure_dirs()
        for paper in s2_papers[:15]:  # Cap at 15 per day
            if paper.get('url') in existing_urls:
                continue
            paper_id = get_paper_id(paper['title'], paper.get('year', 2025))
            paper_path = PAPERS_DIR / f"{paper_id}.yaml"
            if paper_path.exists():
                continue
            relevance = RelevanceScorer.score(paper, interests)
            paper_data = {
                'title': paper['title'],
                'authors': paper.get('authors', []),
                'year': paper.get('year'),
                'abstract': paper.get('abstract', ''),
                'summary': paper.get('summary', ''),
                'url': paper.get('url'),
                'status': 'discovered',
                'source_type': 'semantic_scholar',
                'discovery_reason': paper.get('discovery_reason', ''),
                'citation_count': paper.get('citation_count', 0),
                's2_id': paper.get('s2_id', ''),
                'relevance_score': relevance,
                'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            paper_data = {k: v for k, v in paper_data.items() if v is not None and v != '' and v != []}
            save_yaml(paper_path, paper_data)
            s2_added += 1
            existing_urls.add(paper.get('url', ''))
        log(f"Added {s2_added} papers from Semantic Scholar")
        new_papers_count = len(new_papers) + s2_added
    except Exception as e:
        log(f"Semantic Scholar discovery failed: {e}")
        new_papers_count = len(new_papers)

    # 2. Auto-generate cards
    log("Step 2: Auto-generating cards...")
    cards_generated = auto_generate_cards()
    log(f"Generated cards for {cards_generated} papers")

    # 3. Get review stats
    state = load_review_state()
    stats = SM2.get_stats(state)
    due = stats['due_today']
    total = stats['total']
    mastered = stats['mastered']

    # 4. Send notification
    total_new = new_papers_count if 'new_papers_count' in dir() else len(new_papers)
    if due > 0:
        notify(
            "Knowledge Review",
            f"{due} cards due today. {total_new} new papers found. Run: python3 papers_cli.py serve"
        )
    elif total_new > 0:
        notify(
            "New Papers Found",
            f"{total_new} new papers discovered. No cards due today."
        )

    # 5. Auto-cleanup
    log("Step 3: Disk cleanup...")
    removed_disc = cleanup_old_discoveries()
    removed_pdfs, freed = cleanup_pdfs_for_mastered()
    if removed_disc or removed_pdfs:
        log(f"Cleaned up: {removed_disc} old discoveries, {removed_pdfs} mastered PDFs ({format_size(freed)} freed)")
    usage = get_disk_usage()
    log(f"Disk: {format_size(usage['total'])} (pdfs: {format_size(usage['pdfs'])})")

    total_new = new_papers_count if 'new_papers_count' in dir() else len(new_papers)
    log(f"Summary: {due} due, {total} total, {mastered} mastered, {total_new} new, {cards_generated} auto-carded")
    log("=== Daily automation complete ===\n")


if __name__ == "__main__":
    main()
