#!/usr/bin/env python3
"""Self-contained multi-platform aggregator (fallback engine), recency-first.

Pulls candidates from Reddit, Hacker News, Polymarket and YouTube, restricted
to the last few days, ranks them by recency-aware engagement, de-duplicates,
and writes a JSON list for the LLM step to filter and summarize.

Goal: fresh, same-window content that changes day to day -- not evergreen
all-time-popular items. No API keys are required for any of the four platforms
(Reddit is rate-limited/blocked from datacenter IPs without OAuth).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: the 'requests' package is required (pip install requests)", file=sys.stderr)
    raise

UA = "daily-research-bot/1.0 (github actions; +https://github.com)"
TIMEOUT = 20

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


def make_item(platform, title, url, score, metric, source="", text="", created=None):
    return {
        "platform": platform,
        "title": (title or "").strip(),
        "url": url,
        "score": float(score or 0),
        "metric": metric,
        "source": source,
        "text": (text or "")[:600],
        "created": created,  # epoch seconds, or None if unknown
    }


# --- Platform fetchers (all scoped to the last `days` days) ---------------
_REDDIT_TOKEN: str | None = None


def reddit_token() -> str:
    """App-only OAuth token (client_credentials). Empty string if no creds.

    Reddit blocks unauthenticated JSON from datacenter IPs (403), so OAuth is
    required on GitHub runners. Needs REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET
    from a Reddit app (https://www.reddit.com/prefs/apps, type "script").
    """
    global _REDDIT_TOKEN
    if _REDDIT_TOKEN is not None:
        return _REDDIT_TOKEN
    cid = os.environ.get("REDDIT_CLIENT_ID")
    csec = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not csec:
        _REDDIT_TOKEN = ""
        return ""
    ua = os.environ.get("REDDIT_USER_AGENT", "daily-research:v1.0 (by /u/daily-research-bot)")
    try:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(cid, csec),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": ua}, timeout=TIMEOUT)
        r.raise_for_status()
        _REDDIT_TOKEN = r.json().get("access_token", "") or ""
        log("reddit: OAuth token acquired" if _REDDIT_TOKEN else "reddit: OAuth returned no token")
    except Exception as e:
        log(f"reddit oauth token failed: {e}")
        _REDDIT_TOKEN = ""
    return _REDDIT_TOKEN


def fetch_reddit(cfg, days) -> list[dict]:
    items: list[dict] = []
    tf = "day" if days <= 1 else "week"
    tok = reddit_token()
    if tok:
        base, suffix = "https://oauth.reddit.com", ""
        ua = os.environ.get("REDDIT_USER_AGENT", "daily-research:v1.0 (by /u/daily-research-bot)")
        headers = {"Authorization": f"bearer {tok}", "User-Agent": ua}
    else:
        base, suffix = "https://www.reddit.com", ".json"
        headers = None
        log("reddit: no OAuth creds; falling back to public endpoints (likely 403 on cloud IPs)")

    def collect(children):
        for c in children:
            d = c.get("data", {})
            ups, ncom = d.get("ups", 0), d.get("num_comments", 0)
            items.append(make_item(
                "Reddit", d.get("title", ""),
                "https://www.reddit.com" + d.get("permalink", ""),
                ups + ncom * 2, f"{ups}赞·{ncom}评论",
                source=f"r/{d.get('subreddit', '')}", text=d.get("selftext", ""),
                created=d.get("created_utc")))

    for sub in cfg.get("reddit_subs", []):
        try:
            data = get_json(f"{base}/r/{sub}/top{suffix}",
                            params={"t": tf, "limit": 20, "raw_json": 1}, headers=headers)
            collect(data.get("data", {}).get("children", []))
            time.sleep(0.5)
        except Exception as e:
            log(f"reddit sub r/{sub} failed: {e}")
    for q in cfg.get("queries", []):
        try:
            data = get_json(f"{base}/search{suffix}",
                            params={"q": q, "sort": "top", "t": tf, "limit": 12,
                                    "type": "link", "raw_json": 1}, headers=headers)
            collect(data.get("data", {}).get("children", []))
            time.sleep(0.5)
        except Exception as e:
            log(f"reddit search '{q}' failed: {e}")
    return items


def fetch_hackernews(cfg, days) -> list[dict]:
    items: list[dict] = []
    cutoff = int(time.time()) - days * 86400
    for q in cfg.get("queries", []):
        try:
            # search_by_date = recency-ordered; we re-rank by points below
            data = get_json("https://hn.algolia.com/api/v1/search_by_date",
                            params={"query": q, "tags": "story",
                                    "numericFilters": f"created_at_i>{cutoff}",
                                    "hitsPerPage": 20})
            for h in data.get("hits", []):
                pts, ncom = h.get("points") or 0, h.get("num_comments") or 0
                url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                items.append(make_item(
                    "Hacker News", h.get("title") or h.get("story_title", ""),
                    url, pts + ncom, f"{pts}分·{ncom}评论", source="news.ycombinator.com",
                    created=h.get("created_at_i")))
        except Exception as e:
            log(f"hn search '{q}' failed: {e}")
    return items


def fetch_polymarket(cfg, days) -> list[dict]:
    items: list[dict] = []
    kws = [k.lower() for k in cfg.get("polymarket_queries", [])]
    try:
        events = get_json("https://gamma-api.polymarket.com/events",
                          params={"closed": "false", "active": "true", "limit": 120})
    except Exception as e:
        log(f"polymarket events failed: {e}")
        return items
    for ev in events if isinstance(events, list) else events.get("data", []):
        title = ev.get("title", "")
        if kws and not any(k in title.lower() for k in kws):
            continue

        def fnum(*keys):
            for k in keys:
                v = ev.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return 0.0

        vol24 = fnum("volume24hr", "volume_24hr", "volume24Hr")
        vol = fnum("volume", "volumeNum")
        score = vol24 if vol24 else vol  # rank by *recent* activity
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
                    odds = f"赔率{round(float(prices[0]) * 100)}%"
                except (TypeError, ValueError, IndexError):
                    odds = ""
        slug = ev.get("slug", "")
        vol_disp = f"24h成交{_human(vol24)}" if vol24 else f"成交{_human(vol)}"
        items.append(make_item(
            "Polymarket", title,
            f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            score, vol_disp + (f"·{odds}" if odds else ""),
            source="polymarket.com"))
    _ = days
    return items


def _human(n: float) -> str:
    n = float(n or 0)
    if n >= 1e8:
        return f"{n / 1e8:.1f}亿"
    if n >= 1e4:
        return f"{n / 1e4:.0f}万"
    return f"{int(n)}"


def fetch_youtube(cfg, days) -> list[dict]:
    items: list[dict] = []
    if not shutil.which("yt-dlp"):
        log("yt-dlp not installed; skipping YouTube")
        return items
    dateafter = (datetime.now(timezone.utc) - timedelta(days=days + 1)).strftime("%Y%m%d")
    for q in cfg.get("yt_queries", []):
        try:
            out = subprocess.run(
                ["yt-dlp", f"ytsearch12:{q}", "--dump-json", "--no-warnings",
                 "--dateafter", dateafter, "--ignore-errors"],
                capture_output=True, text=True, timeout=240)
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
                url = v.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                if not url:
                    continue
                created = None
                ud = v.get("upload_date")  # YYYYMMDD
                if ud:
                    try:
                        created = datetime.strptime(ud, "%Y%m%d").replace(
                            tzinfo=timezone.utc).timestamp()
                    except ValueError:
                        created = None
                items.append(make_item(
                    "YouTube", v.get("title", ""), url, vc,
                    f"{_human(vc)}观看" if vc else "新发布",
                    source=v.get("channel") or v.get("uploader", ""), created=created))
        except Exception as e:
            log(f"youtube search '{q}' failed: {e}")
    return items


FETCHERS = {
    "reddit": fetch_reddit,
    "hackernews": fetch_hackernews,
    "polymarket": fetch_polymarket,
    "youtube": fetch_youtube,
}


def dedupe_and_rank(items: list[dict], days: int) -> list[dict]:
    cutoff = time.time() - days * 86400
    # drop anything we KNOW is older than the window (unknown-date items kept)
    items = [it for it in items if it.get("created") is None or it["created"] >= cutoff]

    by_platform: dict[str, list[dict]] = {}
    for it in items:
        by_platform.setdefault(it["platform"], []).append(it)
    for plist in by_platform.values():
        plist.sort(key=lambda x: x["score"], reverse=True)
        n = len(plist)
        for i, it in enumerate(plist):
            it["rank_pct"] = round(1 - i / n, 3) if n > 1 else 1.0

    seen_url, seen_title, out = set(), set(), []
    for it in sorted(items, key=lambda x: x.get("rank_pct", 0), reverse=True):
        if not it["title"] or not it["url"]:
            continue
        u = (it["url"] or "").rstrip("/")
        nt = norm_title(it["title"])
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
    ap.add_argument("--days", type=int, default=3, help="recency window in days")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    cfg = TOPICS[args.task]
    all_items: list[dict] = []
    for platform in cfg["platforms"]:
        fetcher = FETCHERS.get(platform)
        if not fetcher:
            continue
        log(f"fetching {platform} (last {args.days}d) ...")
        got = fetcher(cfg, args.days)
        log(f"  {platform}: {len(got)} raw items")
        all_items.extend(got)

    ranked = dedupe_and_rank(all_items, args.days)[: args.limit]
    payload = {
        "task": args.task,
        "title": cfg["title"],
        "window_days": args.days,
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
