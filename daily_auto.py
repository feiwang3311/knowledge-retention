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
    load_paper, save_cards, register_cards,
    run_daily_check, cleanup_old_discoveries, cleanup_pdfs_for_mastered,
    get_disk_usage, format_size,
)

LOG_FILE = Path(__file__).parent / "daily.log"
AUTO_GENERATE_THRESHOLD = 0.4  # Generate cards for papers with relevance >= this


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
    """Generate cards for papers that have none and are above relevance threshold."""
    papers = load_all_papers()
    all_cards = load_all_cards()
    generated = 0

    for pid, paper in papers.items():
        # Skip if already has cards
        if pid in all_cards:
            continue

        # Skip low-relevance discovered papers
        status = paper.get('status', 'unread')
        relevance = paper.get('relevance_score', 0)

        # Auto-generate for: manually added papers (any status) or high-relevance discoveries
        is_manual = status in ('unread', 'reading', 'read', 'queued')
        is_high_relevance = relevance >= AUTO_GENERATE_THRESHOLD

        if not (is_manual or is_high_relevance):
            continue

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

    # 1. Fetch new papers
    log("Step 1: Checking arXiv feeds...")
    try:
        new_papers = run_daily_check()
        log(f"Found {len(new_papers)} new papers")
    except Exception as e:
        log(f"Feed check failed: {e}")
        new_papers = []

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
    if due > 0:
        notify(
            "Knowledge Review",
            f"{due} cards due today. {len(new_papers)} new papers found. Run: python3 papers_cli.py serve"
        )
    elif new_papers:
        notify(
            "New Papers Found",
            f"{len(new_papers)} new papers discovered. No cards due today."
        )

    # 5. Auto-cleanup
    log("Step 3: Disk cleanup...")
    removed_disc = cleanup_old_discoveries()
    removed_pdfs, freed = cleanup_pdfs_for_mastered()
    if removed_disc or removed_pdfs:
        log(f"Cleaned up: {removed_disc} old discoveries, {removed_pdfs} mastered PDFs ({format_size(freed)} freed)")
    usage = get_disk_usage()
    log(f"Disk: {format_size(usage['total'])} (pdfs: {format_size(usage['pdfs'])})")

    log(f"Summary: {due} due, {total} total, {mastered} mastered, {len(new_papers)} new, {cards_generated} auto-carded")
    log("=== Daily automation complete ===\n")


if __name__ == "__main__":
    main()
