#!/usr/bin/env python3
"""Convert an archive brief (markdown) into a Telegram-friendly HTML message.

Telegram doesn't render Markdown headings/bold the way GitHub does, so dumping
raw markdown leaks '#', '##', '**', '---' as literal characters. This produces
clean Telegram HTML: a compact header, and per item a bold clickable title, a
de-emphasized source/metric line (with a per-platform emoji), and a short
summary.

Usage: md_to_telegram.py <brief.md>   (writes HTML to stdout)
"""
from __future__ import annotations

import html
import re
import sys

PLATFORM_EMOJI = {
    "reddit": "👽",
    "hacker news": "🟠",
    "hackernews": "🟠",
    "youtube": "▶️",
    "polymarket": "📈",
}

HR_RE = re.compile(r"^[-*_]{3,}$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def esc_attr(s: str) -> str:
    return html.escape(s, quote=True)


def rich(s: str) -> str:
    """Escape, then turn **bold** into <b>bold</b> and drop stray '*' markers."""
    s = esc(s)
    s = BOLD_RE.sub(r"<b>\1</b>", s)
    return s.replace("**", "")


def platform_emoji(metric: str) -> str:
    first = re.split(r"[·|｜]", metric, 1)[0].strip().lower() if metric else ""
    for key, emo in PLATFORM_EMOJI.items():
        if key in first:
            return emo
    return "🔹"


def is_hr(s: str) -> bool:
    return bool(HR_RE.match(s.strip()))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: md_to_telegram.py <brief.md>", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        lines = f.read().splitlines()

    preamble: list[str] = []
    items: list[list[str]] = []
    cur: list[str] | None = None
    for ln in lines:
        if ln.startswith("## "):
            if cur is not None:
                items.append(cur)
            cur = [ln]
        elif cur is None:
            preamble.append(ln)
        else:
            cur.append(ln)
    if cur is not None:
        items.append(cur)

    out: list[str] = []

    for ln in preamble:
        s = ln.strip()
        if not s or is_hr(s):
            continue
        if s.startswith("# "):
            out.append(f"📊 <b>{rich(s[2:].strip())}</b>")
        elif s.startswith(">"):
            c = s.lstrip("> ").strip()
            if "注" in c[:3]:
                out.append(f"⚠️ {rich(c)}")
            else:
                out.append(rich(c))
        else:
            out.append(rich(s))
    out.append("")

    for it in items:
        title = it[0][3:].strip()
        url = None
        metric = None
        summary: list[str] = []
        for ln in it[1:]:
            s = ln.strip()
            if not s or is_hr(s):
                continue
            m = re.search(r"原文链接[:：]\s*(\S+)", s)
            if m:
                url = m.group(1)
                continue
            if url is None and re.match(r"https?://\S+$", s):
                url = s
                continue
            if metric is None and s.startswith("**"):
                metric = s.replace("**", "").strip()
                continue
            summary.append(s)

        # title is already wrapped in <b>; strip bold markers to avoid nested <b>
        title_txt = BOLD_RE.sub(r"\1", esc(title)).replace("**", "")
        if url:
            out.append(f'<b><a href="{esc_attr(url)}">{title_txt}</a></b>')
        else:
            out.append(f"<b>{title_txt}</b>")
        if metric:
            out.append(f"{platform_emoji(metric)} <i>{rich(metric)}</i>")
        if summary:
            out.append(rich(" ".join(summary)))
        out.append("")

    sys.stdout.write("\n".join(out).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
