# reddit-financial-aggregator

# Reddit Financial Aggregator

A self-hosted tool that aggregates trending posts and ticker mentions from financial subreddits. Helps you stay on top of discussions across multiple communities without manually checking each one.

Read-only — no posting, commenting, voting, or interacting with users.

## What It Does

- Fetches recent posts from r/wallstreetbets, r/stocks, r/investing, and r/options (configurable)
- Extracts stock ticker mentions from post titles and bodies ($AAPL, $TSLA, etc.)
- Filters against a watchlist so you only see tickers you care about
- Outputs structured JSON for integration with other tools
- Runs on your schedule via cron or any task scheduler

## Two Modes

| Mode | Auth Required | Rate Limit | Best For |
|------|--------------|------------|----------|
| **PRAW** (default) | Yes — Reddit API credentials | 100 req/min | Production use, reliable |
| **Public JSON** (fallback) | No | ~10 req/min | Quick testing, no signup |

The fetcher tries PRAW first. If credentials aren't set, it falls back to public JSON automatically.

## Setup

### Requirements

Python 3.9+

```bash
pip install praw httpx
```

### Reddit API Credentials (optional but recommended)

1. Go to https://old.reddit.com/prefs/apps/
2. Click "create another app..."
3. Select **script**, set redirect URI to `http://localhost:8080`
4. Copy your client ID (under "personal use script") and secret

```bash
export REDDIT_CLIENT_ID=your_client_id
export REDDIT_CLIENT_SECRET=your_client_secret
export REDDIT_USER_AGENT="linux:reddit-financial-aggregator:v1.0 (by /u/your_username)"
```

Or create a `.env` file (never commit this):

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=linux:reddit-financial-aggregator:v1.0 (by /u/your_username)
```

### No Credentials? No Problem

Skip the above and the fetcher uses Reddit's public JSON endpoints. Lower rate limits but works immediately:

```bash
python reddit_fetcher.py --mode public
```

## Usage

### Command Line

```bash
# Fetch hot posts from default subreddits (auto-detects credentials)
python reddit_fetcher.py

# Specific subreddits and sort
python reddit_fetcher.py --subreddits wallstreetbets stocks --sort new

# Only report specific tickers
python reddit_fetcher.py --watchlist AAPL TSLA NVDA AMD MSFT

# Save to file
python reddit_fetcher.py --output posts.json

# Verbose logging
python reddit_fetcher.py --verbose

# Force public mode (no credentials needed)
python reddit_fetcher.py --mode public --limit 10
```

### As a Library

```python
from reddit_fetcher import RedditFetcher

# Auto-detect credentials from environment
fetcher = RedditFetcher.from_env(
    watchlist={"AAPL", "TSLA", "NVDA", "AMD", "MSFT"},
    posts_per_subreddit=25,
    sort="hot",
)

# Fetch all posts
posts = fetcher.fetch_all()

for post in posts:
    if post.ticker_mentions:
        print(f"r/{post.subreddit} | {post.title}")
        print(f"  Tickers: {', '.join(post.ticker_mentions)}")
        print(f"  Score: {post.score} | Comments: {post.num_comments}")
        print()
```

### Scheduled via Cron

```cron
# Every 4 hours on weekdays, 8 AM - 8 PM ET
0 8,12,16,20 * * 1-5 cd /path/to/repo && python reddit_fetcher.py --output /data/reddit_posts.json
```

## Output Format

```json
[
  {
    "id": "1abc123",
    "subreddit": "wallstreetbets",
    "title": "NVDA earnings play — loading up on calls",
    "body_snippet": "Looking at the IV crush from last quarter...",
    "score": 1247,
    "num_comments": 389,
    "url": "https://www.reddit.com/r/wallstreetbets/comments/...",
    "permalink": "https://www.reddit.com/r/wallstreetbets/comments/...",
    "created_utc": 1710700000.0,
    "ticker_mentions": ["NVDA"],
    "fetched_at": "2026-03-17T18:00:00+00:00"
  }
]
```

## Ticker Detection

Tickers are extracted using two methods:

- **Cashtags** (`$AAPL`, `$TSLA`) — high confidence, always included
- **Bare uppercase** (`NVDA`, `AMD`) — only matched against your watchlist to avoid false positives

A built-in blacklist filters common English words and financial jargon that collide with real tickers (AI, IT, CAR, DD, EV, etc.).

## Integration

This tool outputs JSON that can be consumed by any downstream system. Some examples:

- Pipe into a database for historical tracking
- Feed into a sentiment analysis pipeline
- Display in a dashboard
- Use as context for LLM-based financial analysis

## Configuration

| CLI Flag | Env Var | Default | Description |
|----------|---------|---------|-------------|
| `--subreddits` | — | wallstreetbets, stocks, investing, options | Subreddits to fetch |
| `--sort` | — | hot | Sort method (hot, new, top, rising) |
| `--limit` | — | 25 | Posts per subreddit |
| `--watchlist` | — | None (all tickers) | Only report these tickers |
| `--mode` | — | auto | praw, public, or auto |
| `--output` | — | stdout | Write JSON to file |
| — | `REDDIT_CLIENT_ID` | — | Reddit app client ID |
| — | `REDDIT_CLIENT_SECRET` | — | Reddit app client secret |
| — | `REDDIT_USER_AGENT` | reddit-financial-aggregator/1.0 | User agent string |

## License

MIT
