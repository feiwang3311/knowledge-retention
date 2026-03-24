# Knowledge Retention

A spaced repetition system for academic papers. Automatically generates flashcards from papers using LLMs, then schedules reviews using the SM-2 algorithm (same as Anki) to help you actually remember what you read.

## The Problem

You read papers and technical content but forget them in weeks. Reading summaries is passive and leaves no lasting impression. Daily paper feeds become overwhelming. **Collection is not learning.**

## How It Works

```
Add Paper → Generate Cards (LLM) → Review with Active Recall → Spaced Repetition → Retention
```

1. **Add papers** from arXiv or manually
2. **Auto-generate knowledge cards** (5-8 Q&A pairs per paper) using Claude CLI or Anthropic API
3. **Review with active recall** — answer questions, don't just re-read summaries
4. **Spaced repetition (SM-2)** — cards you forget come back sooner; cards you know space out to weeks/months
5. **Daily discovery** — auto-fetch new papers from arXiv, scored against your interests

## Quick Start

**Requirements:** Python 3.10+, PyYAML, [Claude Code](https://claude.com/claude-code) CLI (for card generation)

```bash
# Clone
git clone https://github.com/feiwang3311/knowledge-retention.git
cd knowledge-retention

# Install dependency
pip install pyyaml

# Create your config files
cp interests.example.yaml interests.yaml   # Edit with your research interests
cp feeds.example.yaml feeds.yaml           # Edit with your arXiv searches

# Add a paper
python3 papers_cli.py add-url https://arxiv.org/abs/2506.20807 -i 5 -t "gpu,optimization"

# Generate knowledge cards (uses Claude Code CLI)
python3 papers_cli.py generate-cards <paper-id>

# Start web UI
python3 papers_cli.py serve
# Open http://127.0.0.1:8234
```

## Web UI

Start the server and open `http://127.0.0.1:8234`. Three tabs:

- **Dashboard** — Cards due today, reading pipeline, review stats
- **Review** — Flashcard session with active recall (Space to reveal, 1-4 to rate)
- **Papers** — Browse all papers, change status, generate cards

**Keyboard shortcuts during review:**
| Key | Action |
|-----|--------|
| Space | Reveal answer |
| 1 | Forgot (comes back tomorrow) |
| 2 | Hard (short interval) |
| 3 | Good (growing interval) |
| 4 | Easy (fast-growing interval) |

## CLI Commands

```bash
# Paper management
python3 papers_cli.py add                     # Add paper interactively
python3 papers_cli.py add-url <arxiv-url>     # Add from arXiv with auto-metadata
python3 papers_cli.py list                    # List all papers
python3 papers_cli.py list --status reading   # Filter by status
python3 papers_cli.py show <id>               # Paper details
python3 papers_cli.py search "attention"      # Search papers
python3 papers_cli.py update <id>             # Update paper fields
python3 papers_cli.py network <id>            # Show paper connections
python3 papers_cli.py by-author               # Group by author
python3 papers_cli.py by-category             # Group by category

# Knowledge retention
python3 papers_cli.py generate-cards <id>     # Generate flashcards via LLM
python3 papers_cli.py review                  # Terminal review session
python3 papers_cli.py daily                   # Check arXiv feeds + show stats
python3 papers_cli.py serve                   # Start web UI on port 8234
```

## Card Generation

Cards are generated using LLM, with two providers:

1. **Claude Code CLI** (default) — Uses your Claude Code subscription via `claude -p`. No extra API key needed.
2. **Anthropic API** (fallback) — Set `ANTHROPIC_API_KEY` environment variable.

Each paper gets 5-8 cards in these categories:
- **Concept** — Core contributions and ideas
- **Comparison** — How it relates to existing work
- **Application** — When and where to use it
- **Limitation** — Open questions and constraints
- **Connection** — Links to other papers in your library

## Daily Automation

Set up a cron job to automatically discover new papers:

```bash
crontab -e
# Add:
0 8 * * * cd /path/to/knowledge-retention && python3 papers_cli.py daily >> daily.log 2>&1
```

Configure searches in `feeds.yaml` and interests in `interests.yaml`.

## File Structure

```
knowledge-retention/
├── papers_cli.py          # CLI entry point (all commands)
├── retention.py           # SM-2 engine + LLM card generator
├── server.py              # HTTP API server
├── review.html            # Single-file web UI
├── categories.yaml        # Paper categories
├── interests.example.yaml # Template for your research interests
├── feeds.example.yaml     # Template for arXiv search configs
│
│  # Created locally (gitignored):
├── papers/                # One YAML per paper
├── cards/                 # One JSON per paper (auto-generated Q&A)
├── pdfs/                  # Downloaded PDFs
├── review_state.json      # SM-2 scheduling state
├── interests.yaml         # Your interests config
└── feeds.yaml             # Your feed config
```

## Paper Status Pipeline

```
discovered → queued → unread → reading → read → reviewing → mastered
```

## License

MIT
