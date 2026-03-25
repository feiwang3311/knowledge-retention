# Knowledge Retention

A personal knowledge management system that automatically discovers papers, generates flashcards using LLMs, and schedules reviews with spaced repetition (SM-2). Includes a web UI with audio playback for passive learning.

## The Problem

You read papers and technical content but forget them in weeks. Reading summaries is passive and leaves no lasting impression. Daily paper feeds become overwhelming. **Collection is not learning.**

## How It Works

```
Discover → Collect → Generate Cards (LLM) → Review → Spaced Repetition → Retention
    ↑                                                                          |
    └── feedback loop (vote good/bad, add topics) ─────────────────────────────┘
```

1. **Smart discovery** — Semantic Scholar API (semantic search, citation graph, recommendations) + arXiv + RSS feeds
2. **Auto-generate knowledge cards** (5-8 per paper) using Claude CLI or Anthropic API
3. **Review with active recall** — flashcard UI with neural TTS audio
4. **Spaced repetition (SM-2)** — cards you forget come back sooner; cards you know space out to weeks/months
5. **Knowledge Radio** — narrative audio playback for passive learning while doing other things
6. **User feedback** — vote on discovered papers to guide future searches

## Quick Start

**Requirements:** Python 3.10+, PyYAML, edge-tts, [Claude Code](https://claude.com/claude-code) CLI (for card generation)

```bash
# Clone
git clone https://github.com/feiwang3311/knowledge-retention.git
cd knowledge-retention

# Install dependencies
pip install pyyaml edge-tts

# Create your config files
cp interests.example.yaml interests.yaml   # Edit with your research interests
cp feeds.example.yaml feeds.yaml           # Edit with your arXiv/RSS searches

# Seed your library with seminal papers (uses LLM + Semantic Scholar)
python3 papers_cli.py seed

# Discover more papers via citation graph and semantic search
python3 papers_cli.py discover

# Generate knowledge cards for a paper
python3 papers_cli.py generate-cards <paper-id>

# Start web UI
python3 papers_cli.py serve
# Open http://127.0.0.1:8234
```

## Web UI

Start the server and open `http://127.0.0.1:8234`. Five tabs:

- **Dashboard** — Cards due today, reading pipeline, review stats
- **Review** — Flashcard session with active recall and neural TTS audio (Space to reveal, 1-4 to rate)
- **Radio** — Knowledge Radio: narrative audio playback of paper summaries and key concepts for passive learning
- **Discover** — Review new papers (thumbs up/down), add papers by URL, add topics in natural language, set priority (Important/Relevant/Hobby)
- **Papers** — Browse all papers, change status, generate cards

**Keyboard shortcuts during review:**
| Key | Action |
|-----|--------|
| Space | Reveal answer |
| 1 | Forgot (comes back tomorrow) |
| 2 | Hard (short interval) |
| 3 | Good (growing interval) |
| 4 | Easy (fast-growing interval) |

## Paper Discovery

Three methods, from broad to precise:

### 1. LLM-Curated Seed Papers
```bash
python3 papers_cli.py seed
```
Uses Claude to suggest the most important/seminal papers for each of your research interests, then finds them on Semantic Scholar. Great for bootstrapping a new topic.

### 2. Smart Discovery (Semantic Scholar)
```bash
python3 papers_cli.py discover
```
- **Semantic search** — finds papers by meaning, not just keywords
- **Recommendations** — "papers like the ones in your library"
- **Citation graph walking** — finds impactful follow-up work to papers you already know

### 3. Daily Feeds (arXiv + RSS)
```bash
python3 papers_cli.py daily
```
Automated daily collection from arXiv searches and RSS feeds (Hacker News, Hugging Face blog, etc.). Configure in `feeds.yaml`.

### User Feedback Loop
The **Discover** tab in the web UI lets you vote on discovered papers (keep/skip) and set priority (Important/Relevant/Hobby). This feedback guides future searches.

## CLI Commands

```bash
# Paper management
python3 papers_cli.py add                     # Add paper interactively
python3 papers_cli.py add-url <url>           # Add from any URL (arXiv, blog, article)
python3 papers_cli.py list                    # List all papers
python3 papers_cli.py list --status reading   # Filter by status
python3 papers_cli.py show <id>               # Paper details
python3 papers_cli.py search "attention"      # Search papers
python3 papers_cli.py update <id>             # Update paper fields
python3 papers_cli.py network <id>            # Show paper connections

# Discovery
python3 papers_cli.py seed                    # LLM suggests seminal papers
python3 papers_cli.py discover                # Semantic Scholar smart discovery
python3 papers_cli.py daily                   # Check arXiv/RSS feeds + S2 discovery

# Knowledge retention
python3 papers_cli.py generate-cards <id>     # Generate flashcards via LLM
python3 papers_cli.py review                  # Terminal review session
python3 papers_cli.py serve                   # Start web UI on port 8234

# Maintenance
python3 papers_cli.py disk                    # Show disk usage
python3 papers_cli.py cleanup                 # Remove old discoveries + mastered PDFs

# Testing
python3 tests.py                              # Run 79-test suite
```

## Card Generation

Cards are generated using LLM, with two providers:

1. **Claude Code CLI** (default) — Uses your Claude Code subscription via `claude -p`. No extra API key needed.
2. **Anthropic API** (fallback) — Set `ANTHROPIC_API_KEY` environment variable.

Each paper gets 5-8 cards using plain language and analogies (not academic jargon):
- **Concept** — Core contributions and ideas
- **Comparison** — How it relates to existing work
- **Application** — When and where to use it
- **Limitation** — Open questions and constraints
- **Connection** — Links to other papers in your library

## Knowledge Radio

Background audio for passive learning. Instead of Q&A flashcards, the radio plays narrative mini-lectures:
- Paper introductions with summaries
- Key concepts explained with context ("A key idea here: ...")
- Comparisons and connections between papers
- New discoveries for audio triage

Uses Microsoft neural TTS (edge-tts) for natural-sounding speech. Play/pause/skip/speed controls in the web UI.

## Daily Automation

Use `daily_auto.py` with macOS launchd or cron:

```bash
# macOS launchd (recommended)
# See the plist in the repo for setup

# Or cron
crontab -e
0 8 * * * cd /path/to/knowledge-retention && python3 daily_auto.py >> daily.log 2>&1
```

Daily automation:
1. Checks arXiv + RSS feeds for new papers
2. Runs Semantic Scholar discovery (semantic search + recommendations + citation graph)
3. Auto-generates cards for top papers (paced at 3/day)
4. Cleans up old discoveries and mastered PDFs
5. Sends macOS notification with review summary

## File Structure

```
knowledge-retention/
├── papers_cli.py          # CLI entry point (all commands)
├── retention.py           # SM-2 engine + LLM card generator + Semantic Scholar
├── server.py              # HTTP API server (threaded)
├── review.html            # Single-file web UI (5 tabs)
├── daily_auto.py          # Daily automation script
├── tests.py               # 79-test suite
├── categories.yaml        # Paper categories
├── interests.example.yaml # Template for research interests
├── feeds.example.yaml     # Template for feed configs
│
│  # Created locally (gitignored):
├── papers/                # One YAML per paper
├── cards/                 # One JSON per paper (auto-generated Q&A)
├── pdfs/                  # Downloaded PDFs
├── review_state.json      # SM-2 scheduling state
├── feedback.json          # User votes on papers
├── topics.json            # User-defined topics
├── interests.yaml         # Your interests config
├── feeds.yaml             # Your feed config
└── .tts_cache/            # Cached TTS audio files
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Fallback for card generation (optional if using Claude Code CLI) |
| `S2_API_KEY` | Semantic Scholar API key for higher rate limits (optional but recommended, get one at [semanticscholar.org](https://www.semanticscholar.org/product/api#api-key-form)) |
| `RETENTION_PORT` | Server port (default: 8234) |

## Paper Status Pipeline

```
discovered → queued → unread → reading → read → reviewing → mastered
```

## License

MIT
