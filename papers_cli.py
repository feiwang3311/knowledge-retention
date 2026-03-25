#!/usr/bin/env python3
"""
Paper Reading Database CLI
A command-line tool for managing academic papers with YAML storage.
"""

import argparse
import html
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

# Paths
BASE_DIR = Path(__file__).parent
PAPERS_DIR = BASE_DIR / "papers"
PDFS_DIR = BASE_DIR / "pdfs"
CATEGORIES_FILE = BASE_DIR / "categories.yaml"
CONNECTIONS_FILE = BASE_DIR / "connections.yaml"


def ensure_dirs():
    """Create necessary directories if they don't exist."""
    PAPERS_DIR.mkdir(exist_ok=True)
    PDFS_DIR.mkdir(exist_ok=True)


def download_pdf(url, paper_id):
    """Download PDF from URL and save to pdfs directory."""
    pdf_path = PDFS_DIR / f"{paper_id}.pdf"
    try:
        print(f"Downloading PDF...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            with open(pdf_path, 'wb') as f:
                f.write(response.read())
        print(f"✓ PDF saved: {pdf_path.name}")
        return str(pdf_path.relative_to(BASE_DIR))
    except Exception as e:
        print(f"✗ Failed to download PDF: {e}")
        return None


def fetch_arxiv_metadata(url):
    """Fetch paper metadata from arXiv URL."""
    # Extract arXiv ID from URL
    match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', url)
    if not match:
        return None
    arxiv_id = match.group(1)
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    try:
        req = urllib.request.Request(abs_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            html_content = response.read().decode('utf-8')

        # Extract title
        title_match = re.search(r'<meta name="citation_title" content="([^"]+)"', html_content)
        title = html.unescape(title_match.group(1)) if title_match else None

        # Extract authors
        author_matches = re.findall(r'<meta name="citation_author" content="([^"]+)"', html_content)
        authors = [html.unescape(a) for a in author_matches]

        # Extract date/year
        date_match = re.search(r'<meta name="citation_date" content="(\d{4})', html_content)
        year = int(date_match.group(1)) if date_match else None

        # Extract abstract
        abstract_match = re.search(
            r'<meta name="citation_abstract" content="([^"]+)"',
            html_content,
            re.DOTALL
        )
        if not abstract_match:
            abstract_match = re.search(
                r'<blockquote class="abstract[^"]*">\s*<span class="descriptor">[^<]*</span>\s*(.+?)</blockquote>',
                html_content,
                re.DOTALL
            )
        abstract = html.unescape(abstract_match.group(1).strip()) if abstract_match else None

        return {
            'title': title,
            'authors': authors,
            'year': year,
            'abstract': abstract,
            'url': abs_url,
            'pdf_url': pdf_url,
            'arxiv_id': arxiv_id,
        }
    except Exception as e:
        print(f"Error fetching arXiv metadata: {e}")
        return None


def load_yaml(path):
    """Load a YAML file, return empty dict if not exists."""
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        return yaml.safe_load(f) or {}


def save_yaml(path, data):
    """Save data to a YAML file."""
    with open(path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def slugify(text):
    """Convert text to a URL-friendly slug."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')


def get_paper_id(title, year):
    """Generate a paper ID from title and year."""
    slug = slugify(title)[:50]
    return f"{year}-{slug}"


def load_all_papers():
    """Load all papers from the papers directory."""
    papers = {}
    if not PAPERS_DIR.exists():
        return papers
    for f in PAPERS_DIR.glob("*.yaml"):
        paper = load_yaml(f)
        if paper:
            paper_id = f.stem
            paper['id'] = paper_id
            papers[paper_id] = paper
    return papers


def load_categories():
    """Load categories from categories.yaml."""
    data = load_yaml(CATEGORIES_FILE)
    return data.get('categories', [])


def load_connections():
    """Load manual connections from connections.yaml."""
    data = load_yaml(CONNECTIONS_FILE)
    return data.get('connections', [])


def save_connections(connections):
    """Save manual connections to connections.yaml."""
    save_yaml(CONNECTIONS_FILE, {'connections': connections})


# ============ Commands ============

def cmd_add(_args):
    """Add a new paper interactively."""
    ensure_dirs()

    print("=== Add New Paper ===\n")

    title = input("Title: ").strip()
    if not title:
        print("Error: Title is required")
        return

    year_str = input("Year: ").strip()
    try:
        year = int(year_str)
    except ValueError:
        print("Error: Invalid year")
        return

    authors_str = input("Authors (comma-separated): ").strip()
    authors = [a.strip() for a in authors_str.split(',') if a.strip()]

    abstract = input("Abstract (optional, press Enter to skip): ").strip()
    url = input("URL (optional): ").strip()
    doi = input("DOI (optional): ").strip()
    pdf_url = input("PDF URL (optional, will download): ").strip()

    tags_str = input("Tags (comma-separated): ").strip()
    tags = [t.strip().lower() for t in tags_str.split(',') if t.strip()]

    # Show available categories
    categories_list = load_categories()
    if categories_list:
        print(f"\nAvailable categories: {', '.join(categories_list)}")
    cats_str = input("Categories (comma-separated): ").strip()
    categories = [c.strip() for c in cats_str.split(',') if c.strip()]

    status = input("Status (unread/reading/read) [unread]: ").strip() or "unread"

    # Interest level
    interest_str = input("Interest level (0-5, optional): ").strip()
    interest_level = None
    if interest_str:
        try:
            interest_level = int(interest_str)
            if interest_level < 0 or interest_level > 5:
                print("Warning: Interest level should be 0-5, keeping value anyway")
        except ValueError:
            print("Warning: Invalid interest level, skipping")

    # Summary
    summary = None
    if abstract:
        use_abstract = input("Use abstract as summary? (y/n) [y]: ").strip().lower()
        if use_abstract != 'n':
            summary = abstract
        else:
            summary = input("Summary (optional): ").strip() or None
    else:
        summary = input("Summary (optional): ").strip() or None

    # Source tracking
    print("\n-- Source (how did you find this paper?) --")
    recommended_by = input("Recommended by (person/org, optional): ").strip()
    found_via_str = input("Found via papers (paper IDs, comma-separated, optional): ").strip()
    found_via_papers = [p.strip() for p in found_via_str.split(',') if p.strip()]

    notes = input("\nPersonal notes (optional): ").strip()

    takeaways_str = input("Key takeaways (comma-separated, optional): ").strip()
    takeaways = [t.strip() for t in takeaways_str.split(',') if t.strip()]

    paper_id = get_paper_id(title, year)
    paper_path = PAPERS_DIR / f"{paper_id}.yaml"

    # Check if exists
    if paper_path.exists():
        overwrite = input(f"\nPaper {paper_id} already exists. Overwrite? (y/n): ")
        if overwrite.lower() != 'y':
            print("Cancelled.")
            return

    # Download PDF if URL provided
    pdf_path = None
    if pdf_url:
        pdf_path = download_pdf(pdf_url, paper_id)

    paper = {
        'title': title,
        'authors': authors,
        'year': year,
        'abstract': abstract if abstract else None,
        'summary': summary,
        'url': url if url else None,
        'doi': doi if doi else None,
        'pdf_path': pdf_path,
        'tags': tags,
        'categories': categories,
        'status': status,
        'interest_level': interest_level,
        'recommended_by': recommended_by if recommended_by else None,
        'found_via_papers': found_via_papers if found_via_papers else None,
        'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'notes': notes if notes else None,
        'key_takeaways': takeaways if takeaways else None,
    }

    # Remove None values for cleaner YAML
    paper = {k: v for k, v in paper.items() if v is not None}

    save_yaml(paper_path, paper)
    print(f"\n✓ Paper saved: {paper_id}")


def cmd_add_url(args):
    """Add a paper from arXiv URL with auto-fetched metadata."""
    ensure_dirs()

    # Check if it's an arXiv URL
    if 'arxiv.org' in args.url:
        print(f"Fetching metadata from arXiv: {args.url}")
        metadata = fetch_arxiv_metadata(args.url)

        if not metadata:
            print("Error: Could not fetch arXiv metadata.")
            return

        print(f"\n=== Found Paper ===")
        print(f"Title: {metadata['title']}")
        print(f"Authors: {', '.join(metadata['authors'])}")
        print(f"Year: {metadata['year']}")

        paper_id = get_paper_id(metadata['title'], metadata['year'])
        paper_path = PAPERS_DIR / f"{paper_id}.yaml"

        if paper_path.exists() and not args.force:
            print(f"\nPaper already exists: {paper_id}")
            print("Use --force to overwrite")
            return

        pdf_path = None
        if args.pdf:
            pdf_path = download_pdf(metadata['pdf_url'], paper_id)

        paper = {
            'title': metadata['title'],
            'authors': metadata['authors'],
            'year': metadata['year'],
            'abstract': metadata['abstract'],
            'summary': metadata['abstract'],
            'url': metadata['url'],
            'pdf_url': metadata['pdf_url'],
            'pdf_path': pdf_path,
            'tags': [t.strip() for t in args.tags.split(',') if t.strip()] if args.tags else [],
            'categories': [c.strip() for c in args.categories.split(',') if c.strip()] if args.categories else [],
            'status': 'unread',
            'source_type': 'arxiv',
            'interest_level': args.interest,
            'recommended_by': args.source,
            'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    else:
        # Generic URL: blog post, article, etc.
        print(f"Scraping web page: {args.url}")
        from retention import scrape_web_page
        scraped = scrape_web_page(args.url)

        if not scraped:
            print("Error: Could not fetch page content.")
            return

        title = scraped.get('title', 'Unknown')
        description = scraped.get('description') or (scraped.get('text') or '')[:300]

        print(f"\n=== Found Content ===")
        print(f"Title: {title}")
        print(f"Description: {description[:100]}...")

        year = datetime.now().year
        paper_id = get_paper_id(title, year)
        paper_path = PAPERS_DIR / f"{paper_id}.yaml"

        if paper_path.exists() and not args.force:
            print(f"\nAlready exists: {paper_id}")
            print("Use --force to overwrite")
            return

        paper = {
            'title': title,
            'authors': [],
            'year': year,
            'abstract': description,
            'summary': description,
            'url': args.url,
            'tags': [t.strip() for t in args.tags.split(',') if t.strip()] if args.tags else [],
            'categories': [c.strip() for c in args.categories.split(',') if c.strip()] if args.categories else [],
            'status': 'unread',
            'source_type': 'web',
            'interest_level': args.interest,
            'recommended_by': args.source,
            'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # Remove None/empty values
    paper = {k: v for k, v in paper.items() if v is not None and v != []}

    save_yaml(paper_path, paper)
    print(f"\n✓ Saved: {paper_id}")


def cmd_list(args):
    """List all papers with optional filters."""
    papers = load_all_papers()

    if not papers:
        print("No papers found. Add some with: python papers_cli.py add")
        return

    # Apply filters
    filtered = papers.values()

    if args.status:
        filtered = [p for p in filtered if p.get('status') == args.status]

    if args.category:
        filtered = [p for p in filtered if args.category in p.get('categories', [])]

    if args.tag:
        filtered = [p for p in filtered if args.tag.lower() in p.get('tags', [])]

    filtered = list(filtered)

    if not filtered:
        print("No papers match the filters.")
        return

    # Sort by year (newest first)
    filtered.sort(key=lambda p: p.get('year', 0), reverse=True)

    print(f"\n{'ID':<40} {'Year':<6} {'Status':<10} Title")
    print("-" * 100)

    for p in filtered:
        paper_id = p.get('id', 'unknown')[:38]
        year = p.get('year', '?')
        status = p.get('status', '?')[:8]
        title = p.get('title', 'Untitled')[:45]
        print(f"{paper_id:<40} {year:<6} {status:<10} {title}")

    print(f"\nTotal: {len(filtered)} papers")


def cmd_show(args):
    """Show details of a specific paper."""
    papers = load_all_papers()

    if args.id not in papers:
        print(f"Paper not found: {args.id}")
        return

    p = papers[args.id]

    print(f"\n{'='*60}")
    print(f"Title: {p.get('title')}")
    print(f"{'='*60}")
    print(f"ID: {p.get('id')}")
    print(f"Authors: {', '.join(p.get('authors', []))}")
    print(f"Year: {p.get('year')}")
    print(f"Status: {p.get('status')}")
    if p.get('interest_level') is not None:
        print(f"Interest: {p.get('interest_level')}/5")
    # Support both old 'date_added' and new 'added_at'
    added = p.get('added_at') or p.get('date_added')
    if added:
        print(f"Added: {added}")

    if p.get('url'):
        print(f"URL: {p.get('url')}")
    if p.get('doi'):
        print(f"DOI: {p.get('doi')}")
    if p.get('pdf_path'):
        print(f"PDF: {p.get('pdf_path')}")

    print(f"\nTags: {', '.join(p.get('tags', []))}")
    print(f"Categories: {', '.join(p.get('categories', []))}")

    # Source info
    if p.get('recommended_by') or p.get('found_via_papers'):
        print(f"\nSource:")
        if p.get('recommended_by'):
            print(f"  Recommended by: {p.get('recommended_by')}")
        if p.get('found_via_papers'):
            print(f"  Found via papers: {', '.join(p.get('found_via_papers', []))}")

    if p.get('summary'):
        print(f"\nSummary:\n{p.get('summary')}")

    if p.get('abstract') and p.get('abstract') != p.get('summary'):
        print(f"\nAbstract:\n{p.get('abstract')}")

    if p.get('notes'):
        print(f"\nNotes:\n{p.get('notes')}")

    if p.get('key_takeaways'):
        print(f"\nKey Takeaways:")
        for t in p.get('key_takeaways', []):
            print(f"  • {t}")


def cmd_search(args):
    """Search papers by query."""
    papers = load_all_papers()
    query = args.query.lower()

    results = []
    for p in papers.values():
        searchable = ' '.join([
            p.get('title', ''),
            p.get('abstract', '') or '',
            p.get('notes', '') or '',
            ' '.join(p.get('authors', [])),
            ' '.join(p.get('tags', [])),
        ]).lower()

        if query in searchable:
            results.append(p)

    if not results:
        print(f"No papers found matching: {args.query}")
        return

    print(f"\nSearch results for '{args.query}':\n")
    for p in results:
        print(f"  [{p.get('year')}] {p.get('title')}")
        print(f"         ID: {p.get('id')}")
        print()


def cmd_update(args):
    """Update a paper's fields."""
    paper_path = PAPERS_DIR / f"{args.id}.yaml"

    if not paper_path.exists():
        print(f"Paper not found: {args.id}")
        return

    paper = load_yaml(paper_path)

    print(f"Updating: {paper.get('title')}")
    print("Press Enter to keep current value\n")

    # Status
    current_status = paper.get('status', 'unread')
    new_status = input(f"Status [{current_status}]: ").strip()
    if new_status:
        paper['status'] = new_status

    # Interest level
    current_interest = paper.get('interest_level', '')
    interest_display = str(current_interest) if current_interest is not None else 'not set'
    new_interest = input(f"Interest level (0-5) [{interest_display}]: ").strip()
    if new_interest:
        try:
            paper['interest_level'] = int(new_interest)
        except ValueError:
            print("Warning: Invalid interest level, skipping")

    # Summary
    current_summary = paper.get('summary', '')
    print(f"Current summary: {current_summary[:100]}..." if current_summary else "No summary")
    new_summary = input("New summary (or 'append:' to add, Enter to skip): ").strip()
    if new_summary.startswith('append:'):
        addition = new_summary[7:].strip()
        paper['summary'] = (paper.get('summary', '') + ' ' + addition).strip()
    elif new_summary:
        paper['summary'] = new_summary

    # Notes
    current_notes = paper.get('notes', '')
    print(f"Current notes: {current_notes[:100]}..." if current_notes else "No notes")
    new_notes = input("New notes (or 'append:' to add): ").strip()
    if new_notes.startswith('append:'):
        addition = new_notes[7:].strip()
        paper['notes'] = (paper.get('notes', '') + '\n' + addition).strip()
    elif new_notes:
        paper['notes'] = new_notes

    # Tags
    current_tags = ', '.join(paper.get('tags', []))
    new_tags = input(f"Tags [{current_tags}]: ").strip()
    if new_tags:
        paper['tags'] = [t.strip().lower() for t in new_tags.split(',')]

    # Categories
    current_cats = ', '.join(paper.get('categories', []))
    new_cats = input(f"Categories [{current_cats}]: ").strip()
    if new_cats:
        paper['categories'] = [c.strip() for c in new_cats.split(',')]

    # Source - recommended by
    current_rec = paper.get('recommended_by', '')
    new_rec = input(f"Recommended by [{current_rec}]: ").strip()
    if new_rec:
        paper['recommended_by'] = new_rec

    # Source - found via papers
    current_via = ', '.join(paper.get('found_via_papers', []))
    print(f"Current found via papers: {current_via}" if current_via else "No source papers")
    new_via = input("Found via papers (paper IDs, comma-separated, or 'append:' to add): ").strip()
    if new_via.startswith('append:'):
        addition = [p.strip() for p in new_via[7:].split(',') if p.strip()]
        existing = paper.get('found_via_papers', []) or []
        paper['found_via_papers'] = existing + addition
    elif new_via:
        paper['found_via_papers'] = [p.strip() for p in new_via.split(',') if p.strip()]

    # Key takeaways
    new_takeaway = input("Add key takeaway (optional): ").strip()
    if new_takeaway:
        takeaways = paper.get('key_takeaways', []) or []
        takeaways.append(new_takeaway)
        paper['key_takeaways'] = takeaways

    save_yaml(paper_path, paper)
    print(f"\n✓ Paper updated: {args.id}")


def cmd_connect(args):
    """Add a manual connection between papers."""
    papers = load_all_papers()

    if args.id1 not in papers:
        print(f"Paper not found: {args.id1}")
        return
    if args.id2 not in papers:
        print(f"Paper not found: {args.id2}")
        return

    conn_type = input("Connection type (e.g., builds-on, related, successor, critique): ").strip()
    note = input("Note (optional): ").strip()

    connections = load_connections()

    new_conn = {
        'papers': [args.id1, args.id2],
        'type': conn_type or 'related',
    }
    if note:
        new_conn['note'] = note

    connections.append(new_conn)
    save_connections(connections)

    print(f"\n✓ Connection added: {args.id1} <-> {args.id2} ({conn_type or 'related'})")


def find_connections(paper_id, papers, manual_connections):
    """Find all connections for a paper (auto + manual)."""
    if paper_id not in papers:
        return []

    paper = papers[paper_id]
    connections = []

    paper_authors = set(paper.get('authors', []))
    paper_tags = set(paper.get('tags', []))
    paper_categories = set(paper.get('categories', []))

    for pid, p in papers.items():
        if pid == paper_id:
            continue

        reasons = []

        # Shared authors
        shared_authors = paper_authors & set(p.get('authors', []))
        if shared_authors:
            reasons.append(f"shared authors: {', '.join(shared_authors)}")

        # Shared tags (2+)
        shared_tags = paper_tags & set(p.get('tags', []))
        if len(shared_tags) >= 2:
            reasons.append(f"shared tags: {', '.join(shared_tags)}")

        # Shared categories
        shared_cats = paper_categories & set(p.get('categories', []))
        if shared_cats:
            reasons.append(f"shared categories: {', '.join(shared_cats)}")

        if reasons:
            connections.append({
                'paper': p,
                'type': 'auto',
                'reasons': reasons
            })

    # Manual connections
    for conn in manual_connections:
        conn_papers = conn.get('papers', [])
        if paper_id in conn_papers:
            other_id = conn_papers[0] if conn_papers[1] == paper_id else conn_papers[1]
            if other_id in papers:
                connections.append({
                    'paper': papers[other_id],
                    'type': 'manual',
                    'conn_type': conn.get('type', 'related'),
                    'note': conn.get('note', '')
                })

    return connections


def cmd_network(args):
    """Show network connections for a paper or all papers."""
    papers = load_all_papers()
    manual_connections = load_connections()

    if args.all:
        # Show summary of all connections
        print("\n=== Paper Network Summary ===\n")

        for paper_id, paper in sorted(papers.items()):
            conns = find_connections(paper_id, papers, manual_connections)
            if conns:
                print(f"{paper.get('title', paper_id)[:50]}")
                print(f"  └─ {len(conns)} connection(s)")

        print(f"\nTotal papers: {len(papers)}")
        print(f"Manual connections: {len(manual_connections)}")
        return

    if not args.id:
        print("Specify a paper ID or use --all")
        return

    if args.id not in papers:
        print(f"Paper not found: {args.id}")
        return

    paper = papers[args.id]
    connections = find_connections(args.id, papers, manual_connections)

    print(f"\n=== Connections for: {paper.get('title')} ===\n")

    if not connections:
        print("No connections found.")
        return

    auto_conns = [c for c in connections if c['type'] == 'auto']
    manual_conns = [c for c in connections if c['type'] == 'manual']

    if auto_conns:
        print("Auto-discovered connections:")
        for c in auto_conns:
            p = c['paper']
            print(f"  • [{p.get('year')}] {p.get('title')}")
            for reason in c['reasons']:
                print(f"      └─ {reason}")
        print()

    if manual_conns:
        print("Manual connections:")
        for c in manual_conns:
            p = c['paper']
            print(f"  • [{p.get('year')}] {p.get('title')}")
            print(f"      └─ type: {c['conn_type']}")
            if c.get('note'):
                print(f"      └─ note: {c['note']}")


def cmd_by_author(_args):
    """Group papers by author."""
    papers = load_all_papers()

    authors = {}
    for p in papers.values():
        for author in p.get('authors', []):
            if author not in authors:
                authors[author] = []
            authors[author].append(p)

    # Sort by paper count
    sorted_authors = sorted(authors.items(), key=lambda x: len(x[1]), reverse=True)

    print("\n=== Papers by Author ===\n")
    for author, author_papers in sorted_authors:
        print(f"{author} ({len(author_papers)} papers)")
        for p in sorted(author_papers, key=lambda x: x.get('year', 0), reverse=True):
            print(f"  • [{p.get('year')}] {p.get('title')[:50]}")
        print()


def cmd_by_category(_args):
    """Group papers by category."""
    papers = load_all_papers()

    categories = {}
    uncategorized = []

    for p in papers.values():
        cats = p.get('categories', [])
        if not cats:
            uncategorized.append(p)
        for cat in cats:
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(p)

    print("\n=== Papers by Category ===\n")

    for cat in sorted(categories.keys()):
        cat_papers = categories[cat]
        print(f"## {cat} ({len(cat_papers)} papers)")
        for p in sorted(cat_papers, key=lambda x: x.get('year', 0), reverse=True):
            print(f"  • [{p.get('year')}] {p.get('title')[:50]}")
        print()

    if uncategorized:
        print(f"## Uncategorized ({len(uncategorized)} papers)")
        for p in uncategorized:
            print(f"  • [{p.get('year')}] {p.get('title')[:50]}")


def cmd_tags(_args):
    """List all tags with counts."""
    papers = load_all_papers()

    tags = {}
    for p in papers.values():
        for tag in p.get('tags', []):
            tags[tag] = tags.get(tag, 0) + 1

    print("\n=== Tags ===\n")
    for tag, count in sorted(tags.items(), key=lambda x: x[1], reverse=True):
        print(f"  {tag}: {count}")


def cmd_delete(args):
    """Delete a paper."""
    paper_path = PAPERS_DIR / f"{args.id}.yaml"

    if not paper_path.exists():
        print(f"Paper not found: {args.id}")
        return

    paper = load_yaml(paper_path)
    confirm = input(f"Delete '{paper.get('title')}'? (y/n): ")

    if confirm.lower() == 'y':
        paper_path.unlink()
        print(f"✓ Deleted: {args.id}")
    else:
        print("Cancelled.")


# ============ Retention Commands ============

def cmd_generate_cards(args):
    """Generate knowledge cards for a paper."""
    from retention import (CardGenerator, load_all_papers, load_paper,
                           save_cards, register_cards)

    paper = load_paper(args.id)
    if not paper:
        print(f"Paper not found: {args.id}")
        return

    all_papers = load_all_papers()
    related = [p for pid, p in all_papers.items() if pid != args.id]

    cards_data = CardGenerator.generate(paper, related)
    if cards_data is None:
        print("Failed to generate cards.")
        return

    save_cards(args.id, cards_data)
    count = register_cards(cards_data)
    print(f"\n✓ Generated {count} cards for: {paper.get('title')}")
    for card in cards_data["cards"]:
        print(f"  [{card['type']}] {card['question'][:70]}")


def cmd_review(_args):
    """Interactive CLI review session."""
    from retention import (SM2, load_review_state, save_review_state,
                           load_all_cards, load_all_papers, get_studied_paper_ids)

    state = load_review_state()
    papers = load_all_papers()
    studied = get_studied_paper_ids(papers)
    due_ids = SM2.get_due_cards(state, studied_paper_ids=studied)

    if not due_ids:
        stats = SM2.get_stats(state, studied)
        print(f"No cards due today! (Total: {stats['total']}, Mastered: {stats['mastered']})")
        return

    all_cards = load_all_cards()

    # Build card list with full data
    cards = []
    for card_id in due_ids:
        card_state = state["cards"][card_id]
        paper_id = card_state["paper_id"]
        paper_cards = all_cards.get(paper_id, {}).get("cards", [])
        card_data = next((c for c in paper_cards if c["id"] == card_id), None)
        if card_data:
            cards.append({
                **card_data,
                "paper_title": papers.get(paper_id, {}).get("title", "Unknown"),
            })

    print(f"\n=== Review Session: {len(cards)} cards due ===\n")

    results = {"forgot": 0, "hard": 0, "good": 0, "easy": 0}

    for i, card in enumerate(cards):
        print(f"--- Card {i+1}/{len(cards)} [{card['type']}] ---")
        print(f"Paper: {card['paper_title']}")
        print(f"\nQ: {card['question']}\n")

        input("  [Press Enter to reveal answer]")
        print(f"\nA: {card['answer']}\n")

        while True:
            rating = input("  Rate: (1) Forgot  (2) Hard  (3) Good  (4) Easy > ").strip()
            if rating in ('1', '2', '3', '4'):
                break
            print("  Please enter 1-4")

        quality_map = {'1': 1, '2': 3, '3': 4, '4': 5}
        quality = quality_map[rating]
        result_names = {'1': 'forgot', '2': 'hard', '3': 'good', '4': 'easy'}
        results[result_names[rating]] += 1

        SM2.schedule(state["cards"][card["id"]], quality)
        print(f"  → Next review in {state['cards'][card['id']]['interval_days']} day(s)\n")

    save_review_state(state)

    print(f"=== Session Complete ===")
    print(f"  Forgot: {results['forgot']}  Hard: {results['hard']}  Good: {results['good']}  Easy: {results['easy']}")


def cmd_daily(_args):
    """Run daily feed check and show review status."""
    from retention import run_daily_check
    run_daily_check()


def cmd_discover(_args):
    """Discover papers using Semantic Scholar (semantic search + recommendations + citation graph)."""
    from retention import (discover_via_semantic_scholar, load_interests,
                           load_all_papers, RelevanceScorer)

    print("=== Smart Paper Discovery ===\n")
    interests = load_interests()
    existing = load_all_papers()
    existing_urls = {p.get('url', '') for p in existing.values()}

    papers = discover_via_semantic_scholar(interests, existing)

    if not papers:
        print("No new papers found.")
        return

    # Save top papers
    added = 0
    for paper in papers:
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
            'tags': [],
            'categories': [],
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
        cites = paper.get('citation_count', 0)
        reason = paper.get('discovery_reason', '')[:40]
        print(f"  + [{cites:>5} cites] {paper['title'][:55]}")
        print(f"               via: {reason}")
        added += 1
        existing_urls.add(paper.get('url', ''))

    print(f"\n=== Added {added} papers ===")


def cmd_seed(_args):
    """Use LLM to suggest seminal papers for your research interests, then find them on Semantic Scholar."""
    from retention import (generate_seed_papers_prompt, load_interests,
                           s2_semantic_search, CardGenerator)
    import subprocess

    interests = load_interests()
    prompt = generate_seed_papers_prompt(interests)

    print("=== Generating seed paper suggestions via LLM ===\n")

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr}")
            return
        response = result.stdout.strip()
    except Exception as e:
        print(f"Error: {e}")
        return

    # Parse response
    cards = CardGenerator._parse_response(response)
    if not cards:
        print("Failed to parse LLM response")
        print(response[:500])
        return

    print(f"LLM suggested {len(cards)} papers:\n")
    for paper in cards:
        title = paper.get('title', '?')
        area = paper.get('area', '?')
        why = paper.get('why', '')
        year = paper.get('year', '?')
        print(f"  [{year}] {title}")
        print(f"         Area: {area}")
        print(f"         Why: {why}")
        print()

    # Now search Semantic Scholar for each and add them
    import time
    added = 0
    existing = load_all_papers()
    existing_urls = {p.get('url', '') for p in existing.values()}

    for paper in cards:
        title = paper.get('title', '')
        if not title:
            continue

        # Search S2 for exact match
        results = s2_semantic_search(f'"{title}"', limit=3)
        time.sleep(1)

        if not results:
            print(f"  Not found on S2: {title[:50]}")
            continue

        # Take the best match
        best = results[0]
        if best.get('url') in existing_urls:
            continue

        paper_id = get_paper_id(best['title'], best.get('year', 2025))
        paper_path = PAPERS_DIR / f"{paper_id}.yaml"
        if paper_path.exists():
            continue

        paper_data = {
            'title': best['title'],
            'authors': best.get('authors', []),
            'year': best.get('year'),
            'abstract': best.get('abstract', ''),
            'summary': best.get('summary', ''),
            'url': best.get('url'),
            'tags': [],
            'categories': [],
            'status': 'discovered',
            'source_type': 'semantic_scholar',
            'discovery_reason': f"seed: {paper.get('area', '')} — {paper.get('why', '')}",
            'citation_count': best.get('citation_count', 0),
            's2_id': best.get('s2_id', ''),
            'added_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        paper_data = {k: v for k, v in paper_data.items() if v is not None and v != '' and v != []}
        save_yaml(paper_path, paper_data)
        print(f"  + [{best.get('citation_count', 0):>5} cites] {best['title'][:55]}")
        added += 1
        existing_urls.add(best.get('url', ''))

    print(f"\n=== Added {added} seed papers ===")


def cmd_serve(_args):
    """Start the web review server."""
    from server import main as server_main
    server_main()


def cmd_cleanup(_args):
    """Clean up disk: remove old discoveries and PDFs for mastered papers."""
    from retention import run_cleanup
    run_cleanup()


def cmd_disk(_args):
    """Show disk usage breakdown."""
    from retention import get_disk_usage, format_size, load_all_papers, load_all_cards
    usage = get_disk_usage()
    papers = load_all_papers()
    all_cards = load_all_cards()

    status_counts = {}
    for p in papers.values():
        s = p.get('status', 'unknown')
        status_counts[s] = status_counts.get(s, 0) + 1

    print(f"\n=== Disk Usage ===")
    print(f"  Papers (YAML):  {format_size(usage['papers'])} ({len(papers)} files)")
    print(f"  Cards (JSON):   {format_size(usage['cards'])} ({len(all_cards)} files)")
    print(f"  PDFs:           {format_size(usage['pdfs'])}")
    print(f"  Total:          {format_size(usage['total'])}")
    print(f"\n=== Paper Status ===")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"\nPDF budget: 500MB")


def main():
    parser = argparse.ArgumentParser(
        description="Paper Reading Database CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python papers_cli.py add                    # Add a new paper
  python papers_cli.py list                   # List all papers
  python papers_cli.py list --status read     # List read papers
  python papers_cli.py show <id>              # Show paper details
  python papers_cli.py search "attention"     # Search papers
  python papers_cli.py network <id>           # Show paper connections
  python papers_cli.py generate-cards <id>    # Generate knowledge cards
  python papers_cli.py review                 # Start review session
  python papers_cli.py daily                  # Run daily feed check
  python papers_cli.py serve                  # Start web UI server
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # add
    subparsers.add_parser('add', help='Add a new paper (interactive)')

    # add-url
    add_url_parser = subparsers.add_parser('add-url', help='Add paper from arXiv URL')
    add_url_parser.add_argument('url', help='Any URL (arXiv, blog, article)')
    add_url_parser.add_argument('--interest', '-i', type=int, help='Interest level (0-5)')
    add_url_parser.add_argument('--categories', '-c', help='Categories (comma-separated)')
    add_url_parser.add_argument('--tags', '-t', help='Tags (comma-separated)')
    add_url_parser.add_argument('--source', '-s', help='Recommended by (person/org)')
    add_url_parser.add_argument('--force', '-f', action='store_true', help='Overwrite if exists')
    add_url_parser.add_argument('--pdf', action='store_true', help='Download PDF (arXiv only, off by default)')

    # list
    list_parser = subparsers.add_parser('list', help='List papers')
    list_parser.add_argument('--status', help='Filter by status')
    list_parser.add_argument('--category', help='Filter by category')
    list_parser.add_argument('--tag', help='Filter by tag')

    # show
    show_parser = subparsers.add_parser('show', help='Show paper details')
    show_parser.add_argument('id', help='Paper ID')

    # search
    search_parser = subparsers.add_parser('search', help='Search papers')
    search_parser.add_argument('query', help='Search query')

    # update
    update_parser = subparsers.add_parser('update', help='Update a paper')
    update_parser.add_argument('id', help='Paper ID')

    # connect
    connect_parser = subparsers.add_parser('connect', help='Add connection between papers')
    connect_parser.add_argument('id1', help='First paper ID')
    connect_parser.add_argument('id2', help='Second paper ID')

    # network
    network_parser = subparsers.add_parser('network', help='Show paper connections')
    network_parser.add_argument('id', nargs='?', help='Paper ID')
    network_parser.add_argument('--all', action='store_true', help='Show all connections')

    # by-author
    subparsers.add_parser('by-author', help='Group papers by author')

    # by-category
    subparsers.add_parser('by-category', help='Group papers by category')

    # tags
    subparsers.add_parser('tags', help='List all tags')

    # delete
    delete_parser = subparsers.add_parser('delete', help='Delete a paper')
    delete_parser.add_argument('id', help='Paper ID')

    # generate-cards
    gc_parser = subparsers.add_parser('generate-cards', help='Generate knowledge cards for a paper')
    gc_parser.add_argument('id', help='Paper ID')

    # review
    subparsers.add_parser('review', help='Interactive review session')

    # daily
    subparsers.add_parser('daily', help='Run daily feed check')

    # discover
    subparsers.add_parser('discover', help='Discover papers via Semantic Scholar')

    # seed
    subparsers.add_parser('seed', help='Use LLM to suggest seminal papers for your interests')

    # serve
    subparsers.add_parser('serve', help='Start web review server')

    # cleanup
    subparsers.add_parser('cleanup', help='Clean up disk (old discoveries + mastered PDFs)')

    # disk
    subparsers.add_parser('disk', help='Show disk usage breakdown')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        'add': cmd_add,
        'add-url': cmd_add_url,
        'list': cmd_list,
        'show': cmd_show,
        'search': cmd_search,
        'update': cmd_update,
        'connect': cmd_connect,
        'network': cmd_network,
        'by-author': cmd_by_author,
        'by-category': cmd_by_category,
        'tags': cmd_tags,
        'delete': cmd_delete,
        'generate-cards': cmd_generate_cards,
        'review': cmd_review,
        'daily': cmd_daily,
        'discover': cmd_discover,
        'seed': cmd_seed,
        'serve': cmd_serve,
        'cleanup': cmd_cleanup,
        'disk': cmd_disk,
    }

    if args.command in commands:
        commands[args.command](args)


if __name__ == '__main__':
    main()
