#!/usr/bin/env python3
"""Self-contained multi-platform aggregator (fallback engine).

Pulls candidate items from Reddit, Hacker News, Polymarket and YouTube,
ranks them by real engagement, de-duplicates, and writes a JSON list of
candidates for the LLM step to filter and summarize.

This is the fallback used when the last30days skill is unavailable. It only
needs `requests` (and optionally the `yt-dlp` binary for YouTube). No API
keys are required for any of the four platforms.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: the 'requests' package is required (pip install requests)", file=sys.stderr)
    raise

UA = "daily-research-bot/1.0 (github actions; +https://github.com)"
TIMEOUT = 20
THIRTY_DAYS = 30 * 86400

# --- Per-task research configuration -------------------------------------
# The human-readable themes live in daily-research/CLAUDE.md; these are the
# concrete queries the deterministic fallback uses.
TOPICS = {
    "ai-tools": {
        "title": "AI工具与变现",
        "platforms": ["reddit", "hackernews", "youtube"],
        "reddit_subs": ["ClaudeAI", "ChatGPTCoding", "LocalLLaMA", "OpenAI",
                         "ChatGPT", "artificial", "singularity", "LLMDevs",
                         "Anthropic", "cursor"],
        "queries": ["AI agent", "Claude Code", "Codex CLI", "agent skills",
                     "SOUL.md", "agent rules best practices",
                     "AI subscription reseller", "sell AI api tokens"],
        "yt_queries": ["Claude Code tips", "AI agents tutorial", "Codex CLI workflow"],
    },
    "money": {
        "title": "搞钱与撸毛",
        "platforms": ["reddit", "polymarket", "youtube"],
        "reddit_subs": ["sidehustle", "passive_income", "Entrepreneur",
                         "juststart", "WorkOnline", "beermoney",
                         "CryptoCurrency", "CryptoMoonShots"],
        "queries": ["side hustle", "make money online", "crypto airdrop",
                     "airdrop guide", "web3 airdrop opportunity"],
        "yt_queries": ["crypto airdrop tutorial", "side hustle ideas"],
        "polymarket_queries": ["crypto", "bitcoin", "ethereum", "airdrop", "token"],
    },
    "uscards": {
        "title": "美卡",
        "platforms": ["reddit", "youtube"],
        "reddit_subs": ["churning", "CreditCards", "awardtravel", "manufacturedspending"],
        "queries": ["sign up bonus", "credit card churning",
                     "best credit card offer", "credit card SUB"],
        "yt_queries": ["credit card churning", "best credit card sign up bonus"],
    },
    "self-improve": {
        "title": "自我提升",
        "platforms": ["reddit", "youtube", "hackernews"],
        "reddit_subs": ["getdisciplined", "Meditation", "productivity",
                         "languagelearning", "EnglishLearning",
                         "decidingtobebetter", "cognitivescience"],
        "queries": ["mental models", "critical thinking habits",
                     "learn English speaking", "English listening practice",
                     "meditation for beginners", "zazen meditation"],
        "yt_queries": ["English speaking practice", "meditation for beginners", "mental models"],
    },
    "foreign-trade": {
        "title": "外贸获客",
        "platforms": ["reddit", "youtube", "hackernews"],
        "reddit_subs": ["sales", "ecommerce", "smallbusiness", "Entrepreneur", "b2bmarketing"],
        "queries": ["B2B lead generation", "find overseas buyers",
                     "export sales leads", "fasteners industry trends",
                     "auto parts sourcing", "hardware sourcing trade"],
        "yt_queries": ["B2B lead generation", "how to find overseas buyers export",
                        "foreign trade customer acquisition"],
    },
}


def log(msg: str) -> None:
    print(f"[aggregate] {msg}", file=sys.stderr)


def get_json(url: str, params: dict | None = None, headers: dict | None = None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def make_item(platform, title, url, score, metric, source="", text=""):
    return {
        "platform": platform,
        "title": (title or "").strip(),
        "url": url,
        "score": float(score or 0),
        "metric": metric,
        "source": source,
        "text": (text or "")[:600],
    }


# --- Platform fetchers ----------------------------------------------------
def fetch_reddit(cfg) -> list[dict]:
    items: list[dict] = []
    for sub in cfg.get("reddit_subs", []):
        try:
            data = get_json(f"https://www.reddit.com/r/{sub}/top.json",
                            params={"t": "week", "limit": 20})
            for c in data.get("data", {}).get("children", []):
                d = c.get("data", {})
                ups, ncom = d.get("ups", 0), d.get("num_comments", 0)
                items.append(make_item(
                    "Reddit", d.get("title", ""),
                    "https://www.reddit.com" + d.get("permalink", ""),
                    ups + ncom * 2, f"{ups}赞 · {ncom}评论",
                    source=f"r/{d.get('subreddit', sub)}", text=d.get("selftext", "")))
            time.sleep(0.5)
        except Exception as e:
            log(f"reddit sub r/{sub} failed: {e}")
    for q in cfg.get("queries", []):
        try:
            data = get_json("https://www.reddit.com/search.json",
                            params={"q": q, "sort": "top", "t": "month", "limit": 12})
            for c in data.get("data", {}).get("children", []):
                d = c.get("data", {})
                ups, ncom = d.get("ups", 0), d.get("num_comments", 0)
                items.append(make_item(
                    "Reddit", d.get("title", ""),
                    "https://www.reddit.com" + d.get("permalink", ""),
                    ups + ncom * 2, f"{ups}赞 · {ncom}评论",
                    source=f"r/{d.get('subreddit', '')}", text=d.get("selftext", "")))
            time.sleep(0.5)
        except Exception as e:
            log(f"reddit search '{q}' failed: {e}")
    return items


def fetch_hackernews(cfg) -> list[dict]:
    items: list[dict] = []
    cutoff = int(time.time()) - THIRTY_DAYS
    for q in cfg.get("queries", []):
        try:
            data = get_json("https://hn.algolia.com/api/v1/search",
                            params={"query": q, "tags": "story",
                                    "numericFilters": f"created_at_i>{cutoff}",
                                    "hitsPerPage": 15})
            for h in data.get("hits", []):
                pts, ncom = h.get("points") or 0, h.get("num_comments") or 0
                url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                items.append(make_item(
                    "Hacker News", h.get("title") or h.get("story_title", ""),
                    url, pts + ncom, f"{pts}分 · {ncom}评论", source="news.ycombinator.com"))
        except Exception as e:
            log(f"hn search '{q}' failed: {e}")
    return items


def fetch_polymarket(cfg) -> list[dict]:
    items: list[dict] = []
    kws = [k.lower() for k in cfg.get("polymarket_queries", [])]
    try:
        events = get_json("https://gamma-api.polymarket.com/events",
                          params={"closed": "false", "active": "true",
                                  "order": "volume", "ascending": "false", "limit": 80})
    except Exception as e:
        log(f"polymarket events failed: {e}")
        return items
    for ev in events if isinstance(events, list) else events.get("data", []):
        title = ev.get("title", "")
        if kws and not any(k in title.lower() for k in kws):
            continue
        vol = ev.get("volume") or 0
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            vol = 0.0
        odds = ""
        markets = ev.get("markets") or []
        if markets:
            prices = markets[0].get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except json.JSONDecodeError:
                    prices = None
            if prices:
                try:
                    odds = f"赔率 {round(float(prices[0]) * 100)}%"
                except (TypeError, ValueError, IndexError):
                    odds = ""
        slug = ev.get("slug", "")
        items.append(make_item(
            "Polymarket", title,
            f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            vol, f"成交量 ${int(vol):,}" + (f" · {odds}" if odds else ""),
            source="polymarket.com"))
    return items


def fetch_youtube(cfg) -> list[dict]:
    items: list[dict] = []
    if not shutil.which("yt-dlp"):
        log("yt-dlp not installed; skipping YouTube")
        return items
    for q in cfg.get("yt_queries", []):
        try:
            out = subprocess.run(
                ["yt-dlp", f"ytsearch8:{q}", "--flat-playlist",
                 "--dump-json", "--no-warnings"],
                capture_output=True, text=True, timeout=150)
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    v = json.loads(line)
                except json.JSONDecodeError:
                    continue
                vc = v.get("view_count") or 0
                vid = v.get("id", "")
                url = v.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                if not url:
                    continue
                items.append(make_item(
                    "YouTube", v.get("title", ""), url, vc,
                    f"{int(vc):,}观看" if vc else "播放量未知",
                    source=v.get("channel") or v.get("uploader", "")))
        except Exception as e:
            log(f"youtube search '{q}' failed: {e}")
    return items


FETCHERS = {
    "reddit": fetch_reddit,
    "hackernews": fetch_hackernews,
    "polymarket": fetch_polymarket,
    "youtube": fetch_youtube,
}


def dedupe_and_rank(items: list[dict]) -> list[dict]:
    seen_url, seen_title, out = set(), set(), []
    # rank within platform first (percentile), so cross-platform compare is fair
    by_platform: dict[str, list[dict]] = {}
    for it in items:
        by_platform.setdefault(it["platform"], []).append(it)
    for plist in by_platform.values():
        plist.sort(key=lambda x: x["score"], reverse=True)
        n = len(plist)
        for i, it in enumerate(plist):
            it["rank_pct"] = round(1 - i / n, 3) if n > 1 else 1.0
    for it in sorted(items, key=lambda x: x.get("rank_pct", 0), reverse=True):
        u = (it["url"] or "").rstrip("/")
        nt = norm_title(it["title"])
        if not it["title"] or not it["url"]:
            continue
        if u in seen_url or (nt and nt in seen_title):
            continue
        seen_url.add(u)
        if nt:
            seen_title.add(nt)
        out.append(it)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=list(TOPICS.keys()))
    ap.add_argument("--out", default="-")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    cfg = TOPICS[args.task]
    all_items: list[dict] = []
    for platform in cfg["platforms"]:
        fetcher = FETCHERS.get(platform)
        if not fetcher:
            continue
        log(f"fetching {platform} ...")
        got = fetcher(cfg)
        log(f"  {platform}: {len(got)} raw items")
        all_items.extend(got)

    ranked = dedupe_and_rank(all_items)[: args.limit]
    payload = {
        "task": args.task,
        "title": cfg["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(ranked),
        "candidates": ranked,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"wrote {len(ranked)} candidates to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
