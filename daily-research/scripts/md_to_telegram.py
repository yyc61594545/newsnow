#!/usr/bin/env python3
"""Convert an archive brief (markdown) into a Telegram-friendly HTML message.

Telegram doesn't render Markdown headings/bold the way GitHub does, so dumping
raw markdown leaks '#', '##', '**' as literal characters. This produces clean
Telegram HTML: a bold header, and per item a bold clickable title, an italic
source/metric line, and the plain summary.

Usage: md_to_telegram.py <brief.md>   (writes HTML to stdout)
"""
from __future__ import annotations

import html
import re
import sys


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def esc_attr(s: str) -> str:
    return html.escape(s, quote=True)


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
        if not s:
            continue
        if s.startswith("# "):
            out.append(f"<b>📊 {esc(s[2:].strip())}</b>")
        elif s.startswith(">"):
            c = s.lstrip("> ").strip()
            if c.startswith("注") or "注：" in c or "注:" in c:
                out.append(f"⚠️ {esc(c)}")
            else:
                out.append(f"<i>{esc(c)}</i>")
        else:
            out.append(esc(s))
    out.append("")

    for it in items:
        title = it[0][3:].strip()
        url = None
        metric = None
        summary: list[str] = []
        for ln in it[1:]:
            s = ln.strip()
            if not s:
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

        if url:
            out.append(f'🔹 <b><a href="{esc_attr(url)}">{esc(title)}</a></b>')
        else:
            out.append(f"🔹 <b>{esc(title)}</b>")
        if metric:
            out.append(f"<i>{esc(metric)}</i>")
        if summary:
            out.append(esc(" ".join(summary)))
        out.append("")

    sys.stdout.write("\n".join(out).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
