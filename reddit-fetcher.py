"""
Reddit Financial Subreddit Aggregator

Fetches trending posts and ticker mentions from financial subreddits.
Read-only — no posting, commenting, or interacting with users.

Supports two modes:
  1. PRAW (authenticated, 100 req/min) — requires Reddit API credentials
  2. Public JSON (unauthenticated, ~10 req/min) — no credentials needed, fallback mode

Usage:
  # With PRAW (set env vars or pass credentials)
  fetcher = RedditFetcher.from_env()

  # Without PRAW (public JSON fallback)
  fetcher = RedditFetcher(mode="public")

  # Fetch posts
  posts = fetcher.fetch_all()
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Default Configuration ─────────────────────────────────────────

DEFAULT_SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "options",
]

# Common tickers that collide with English words — excluded from regex matching
TICKER_BLACKLIST = {
    "A", "AI", "ALL", "AM", "AN", "ANY", "ARE", "AS", "AT", "BE", "BIG",
    "CAN", "CEO", "DD", "DO", "EV", "FOR", "GO", "HAS", "HE", "HER",
    "HIM", "HIS", "HOW", "I", "IF", "IN", "IS", "IT", "ITS", "JUST",
    "KEY", "LET", "LOW", "MAY", "ME", "MET", "MOM", "MY", "NEW", "NO",
    "NOT", "NOW", "OF", "OLD", "ON", "ONE", "OR", "OUR", "OUT", "OWN",
    "PER", "PM", "PSA", "PUT", "RUN", "SAY", "SHE", "SO", "SOS", "TEN",
    "THE", "TO", "TOO", "TWO", "UP", "US", "USA", "WAR", "WAS", "WAY",
    "WE", "WHO", "WHY", "WIN", "WON", "YOU", "CEO", "CFO", "CTO", "COO",
    "ETF", "EPS", "GDP", "IMO", "IPO", "ITM", "OTM", "ATH", "ATL",
    "RSI", "MACD", "YOLO", "FOMO", "HODL", "TLDR", "TL", "DR",
    "EDIT", "UPDATE", "PART", "GAIN", "LOSS", "CALL", "BEAR", "BULL",
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "PUMP", "DUMP", "DIP",
    "RIP", "APE", "MOON", "BAGS", "PLAY", "CASH", "DEBT", "FUND",
    "HIGH", "RISK", "SAFE", "SAVE", "PLAN", "RATE", "REAL", "FREE",
    "GOOD", "BEST", "MUCH", "MOST", "VERY", "WELL", "SOME", "EACH",
    "SAME", "NEXT", "LAST", "OVER", "BACK", "AWAY", "DOWN", "TURN",
    "MOVE", "NEAR", "OPEN", "EVER", "ELSE", "HOPE", "HELP", "EASY",
    "HARD", "FAST", "HUGE", "MEGA", "PURE", "RARE", "TRUE", "FAKE",
    "HALF", "FULL", "WHAT", "WHEN", "WILL", "WITH", "BEEN", "HAVE",
    "FROM", "THAN", "THEY", "THEM", "THEN", "THAT", "THIS", "ONLY",
    "ALSO", "LIKE", "JUST", "EVEN", "TAKE", "MAKE", "COME", "LOOK",
    "WANT", "GIVE", "TELL", "WORK", "KEEP", "KNOW", "NEED", "MEAN",
    "POST", "SUB", "OP", "FYI", "PSA", "LMAO", "LOL",
}

# Regex: $TICKER or standalone 1-5 uppercase letters
TICKER_PATTERN_CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
TICKER_PATTERN_BARE = re.compile(r"\b([A-Z]{1,5})\b")


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class RedditPost:
    """A single post from a financial subreddit."""

    id: str
    subreddit: str
    title: str
    body_snippet: str
    score: int
    num_comments: int
    url: str
    permalink: str
    created_utc: float
    ticker_mentions: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


# ── Ticker Extraction ─────────────────────────────────────────────

def extract_tickers(
    text: str,
    watchlist: Optional[set[str]] = None,
) -> list[str]:
    """
    Extract potential stock tickers from text.

    Priority:
      1. Cashtags ($AAPL, $TSLA) — high confidence
      2. Bare uppercase words matching watchlist — medium confidence
      3. Bare uppercase words not in blacklist — low confidence (optional)

    Args:
        text: Post title + body to scan.
        watchlist: If provided, only return tickers in this set.
                   If None, return all non-blacklisted matches.

    Returns:
        Deduplicated list of ticker strings.
    """
    found = set()

    # Cashtags are high confidence
    for match in TICKER_PATTERN_CASHTAG.finditer(text):
        ticker = match.group(1)
        if watchlist is None or ticker in watchlist:
            if ticker not in TICKER_BLACKLIST:
                found.add(ticker)

    # Bare uppercase — only if on watchlist (too noisy otherwise)
    if watchlist:
        for match in TICKER_PATTERN_BARE.finditer(text):
            ticker = match.group(1)
            if ticker in watchlist and ticker not in TICKER_BLACKLIST:
                found.add(ticker)

    return sorted(found)


# ── Fetcher ───────────────────────────────────────────────────────

class RedditFetcher:
    """
    Fetches posts from financial subreddits.

    Supports two modes:
      - "praw": Uses PRAW library with OAuth credentials (recommended)
      - "public": Uses Reddit's public .json endpoints (no auth, lower rate limit)
    """

    def __init__(
        self,
        mode: str = "praw",
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        user_agent: Optional[str] = None,
        subreddits: Optional[list[str]] = None,
        watchlist: Optional[set[str]] = None,
        posts_per_subreddit: int = 25,
        sort: str = "hot",
    ):
        self.mode = mode
        self.subreddits = subreddits or DEFAULT_SUBREDDITS
        self.watchlist = watchlist
        self.posts_per_subreddit = posts_per_subreddit
        self.sort = sort
        self._reddit = None

        if mode == "praw":
            self._init_praw(client_id, client_secret, user_agent)

    def _init_praw(
        self,
        client_id: Optional[str],
        client_secret: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        """Initialize PRAW in read-only mode."""
        try:
            import praw
        except ImportError:
            raise ImportError(
                "PRAW is required for authenticated mode. "
                "Install it with: pip install praw\n"
                "Or use mode='public' for unauthenticated access."
            )

        if not client_id or not client_secret:
            raise ValueError(
                "client_id and client_secret are required for PRAW mode. "
                "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET env vars, "
                "or use mode='public'."
            )

        self._reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent or "reddit-financial-aggregator/1.0",
        )
        # Force read-only — we never need write access
        self._reddit.read_only = True
        logger.info("PRAW initialized in read-only mode")

    @classmethod
    def from_env(
        cls,
        subreddits: Optional[list[str]] = None,
        watchlist: Optional[set[str]] = None,
        posts_per_subreddit: int = 25,
        sort: str = "hot",
    ) -> "RedditFetcher":
        """
        Create a fetcher from environment variables.

        Falls back to public mode if PRAW credentials are not set.

        Env vars:
            REDDIT_CLIENT_ID
            REDDIT_CLIENT_SECRET
            REDDIT_USER_AGENT
        """
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        user_agent = os.environ.get("REDDIT_USER_AGENT")

        if client_id and client_secret:
            logger.info("Reddit credentials found, using PRAW mode")
            mode = "praw"
        else:
            logger.warning(
                "Reddit credentials not found (REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET). "
                "Falling back to public JSON mode (lower rate limits)."
            )
            mode = "public"

        return cls(
            mode=mode,
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            subreddits=subreddits,
            watchlist=watchlist,
            posts_per_subreddit=posts_per_subreddit,
            sort=sort,
        )

    # ── PRAW fetching ─────────────────────────────────────────────

    def _fetch_subreddit_praw(self, subreddit_name: str) -> list[RedditPost]:
        """Fetch posts from a single subreddit using PRAW."""
        sub = self._reddit.subreddit(subreddit_name)

        sort_methods = {
            "hot": sub.hot,
            "new": sub.new,
            "top": sub.top,
            "rising": sub.rising,
        }
        method = sort_methods.get(self.sort, sub.hot)

        posts = []
        for submission in method(limit=self.posts_per_subreddit):
            text = f"{submission.title} {submission.selftext or ''}"
            body = (submission.selftext or "")[:500]

            posts.append(RedditPost(
                id=submission.id,
                subreddit=subreddit_name,
                title=submission.title,
                body_snippet=body,
                score=submission.score,
                num_comments=submission.num_comments,
                url=submission.url,
                permalink=f"https://www.reddit.com{submission.permalink}",
                created_utc=submission.created_utc,
                ticker_mentions=extract_tickers(text, self.watchlist),
            ))

        return posts

    # ── Public JSON fetching ──────────────────────────────────────

    def _fetch_subreddit_public(self, subreddit_name: str) -> list[RedditPost]:
        """Fetch posts from a single subreddit using public JSON endpoints."""
        import httpx

        url = f"https://www.reddit.com/r/{subreddit_name}/{self.sort}.json"
        params = {"limit": self.posts_per_subreddit, "raw_json": 1}
        headers = {"User-Agent": "reddit-financial-aggregator/1.0 (public-json-fallback)"}

        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=15, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch r/{subreddit_name}: {e}")
            return []

        data = resp.json()
        children = data.get("data", {}).get("children", [])

        posts = []
        for child in children:
            d = child.get("data", {})
            text = f"{d.get('title', '')} {d.get('selftext', '')}"
            body = (d.get("selftext") or "")[:500]

            posts.append(RedditPost(
                id=d.get("id", ""),
                subreddit=subreddit_name,
                title=d.get("title", ""),
                body_snippet=body,
                score=d.get("score", 0),
                num_comments=d.get("num_comments", 0),
                url=d.get("url", ""),
                permalink=f"https://www.reddit.com{d.get('permalink', '')}",
                created_utc=d.get("created_utc", 0),
                ticker_mentions=extract_tickers(text, self.watchlist),
            ))

        return posts

    # ── Main interface ────────────────────────────────────────────

    def fetch_subreddit(self, subreddit_name: str) -> list[RedditPost]:
        """Fetch posts from a single subreddit."""
        if self.mode == "praw":
            return self._fetch_subreddit_praw(subreddit_name)
        else:
            return self._fetch_subreddit_public(subreddit_name)

    def fetch_all(self) -> list[RedditPost]:
        """
        Fetch posts from all configured subreddits.

        Returns deduplicated posts (cross-posted content appears once).
        """
        all_posts: list[RedditPost] = []
        seen_ids: set[str] = set()

        for sub in self.subreddits:
            logger.info(f"Fetching r/{sub} ({self.sort}, limit={self.posts_per_subreddit})")
            try:
                posts = self.fetch_subreddit(sub)
                for post in posts:
                    if post.id not in seen_ids:
                        seen_ids.add(post.id)
                        all_posts.append(post)
                logger.info(f"  r/{sub}: {len(posts)} posts fetched")
            except Exception as e:
                logger.error(f"  r/{sub}: failed — {e}")

            # Polite delay between subreddits (especially for public mode)
            if self.mode == "public":
                time.sleep(2)

        logger.info(f"Total: {len(all_posts)} unique posts from {len(self.subreddits)} subreddits")
        return all_posts

    def fetch_all_as_json(self) -> str:
        """Fetch all posts and return as JSON string."""
        posts = self.fetch_all()
        return json.dumps([p.to_dict() for p in posts], indent=2)


# ── CLI ───────────────────────────────────────────────────────────

def main():
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch trending posts from financial subreddits"
    )
    parser.add_argument(
        "--subreddits",
        nargs="+",
        default=DEFAULT_SUBREDDITS,
        help=f"Subreddits to fetch (default: {', '.join(DEFAULT_SUBREDDITS)})",
    )
    parser.add_argument(
        "--sort",
        choices=["hot", "new", "top", "rising"],
        default="hot",
        help="Sort method (default: hot)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Posts per subreddit (default: 25)",
    )
    parser.add_argument(
        "--watchlist",
        nargs="+",
        default=None,
        help="Only report these tickers (e.g. AAPL TSLA NVDA AMD)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "praw", "public"],
        default="auto",
        help="Fetch mode: auto (try PRAW, fall back to public), praw, or public",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write JSON output to file instead of stdout",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    watchlist = set(args.watchlist) if args.watchlist else None

    if args.mode == "auto":
        fetcher = RedditFetcher.from_env(
            subreddits=args.subreddits,
            watchlist=watchlist,
            posts_per_subreddit=args.limit,
            sort=args.sort,
        )
    else:
        fetcher = RedditFetcher(
            mode=args.mode,
            client_id=os.environ.get("REDDIT_CLIENT_ID"),
            client_secret=os.environ.get("REDDIT_CLIENT_SECRET"),
            user_agent=os.environ.get("REDDIT_USER_AGENT"),
            subreddits=args.subreddits,
            watchlist=watchlist,
            posts_per_subreddit=args.limit,
            sort=args.sort,
        )

    output = fetcher.fetch_all_as_json()

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        logger.info(f"Output written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
