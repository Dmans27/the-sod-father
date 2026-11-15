#!/usr/bin/env python3
"""
Turn a Markdown file into a story draft for the site.

    python3 tools/new_story.py path/to/story.md            # writes a DRAFT
    python3 tools/new_story.py path/to/story.md --publish  # writes it published
    python3 tools/new_story.py story.md --out /some/dir    # try it somewhere else

It writes content/articles/<slug>.json in the same shape the admin saves, so the
story shows up in the admin (as a draft) where you can add photos/place cards,
check it, and press Publish. Nothing is published unless you pass --publish.

Markdown format
---------------
A block of settings at the top between two lines of ---:

    ---
    title: Eating my way through Vienna
    dek: One-sentence summary under the title
    category: travel
    subcategory: destinations
    author: daniel-mansfield
    date: 2026-11-15
    ---

Then the story:

    Plain paragraph text.                    -> paragraph
    ## A subheading                          -> subheading
    - bullet one                             -> bulleted list (consecutive lines)
    1. step one                              -> numbered list
    > A quote | Who said it                  -> quote (attribution after the |)
    !tip: Helpful advice                     -> tip box
    !warning: Watch out for this             -> warning box
    !takeaways Key takeaways                 -> key-takeaways box; the bullets
    - first point                               right under it become the items
    - second point
    [place: listing-slug | Caption]          -> place card from the ai-app
    ***                                      -> divider
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import content  # noqa: E402  (stdlib-only module; gives CATEGORIES and read time)

PLACE_BASE = "https://ai-app-repo.onrender.com"


def slugify(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return re.sub(r"-{2,}", "-", text).strip("-") or "story"


def split_front_matter(raw):
    raw = raw.lstrip("﻿")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.S)
    if not m:
        sys.exit("The file must start with a --- settings block (title, category, ...).")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, m.group(2)


BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
NUMBER = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def parse_blocks(body):
    lines = body.splitlines()
    blocks = []
    i = 0
    pending_takeaways = None

    def flush_para(buf):
        text = " ".join(s.strip() for s in buf if s.strip())
        if text:
            blocks.append({"type": "paragraph", "text": text})

    para = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para(para); para = []
            i += 1
            continue

        m = re.match(r"^!takeaways\s*(.*)$", stripped, re.I)
        if m:
            flush_para(para); para = []
            title = m.group(1).strip() or "Key takeaways"
            items = []
            i += 1
            while i < len(lines) and BULLET.match(lines[i]):
                items.append(BULLET.match(lines[i]).group(1).strip())
                i += 1
            if items:
                blocks.append({"type": "takeaways", "title": title, "items": items})
            continue

        m = re.match(r"^!(tip|warning)\s*:\s*(.+)$", stripped, re.I)
        if m:
            flush_para(para); para = []
            blocks.append({"type": "tip", "variant": m.group(1).lower(), "text": m.group(2).strip()})
            i += 1
            continue

        if re.match(r"^(\*{3,}|_{3,}|-{3,})$", stripped):
            flush_para(para); para = []
            blocks.append({"type": "divider"})
            i += 1
            continue

        m = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if m:
            flush_para(para); para = []
            blocks.append({"type": "subheading", "text": m.group(1).strip()})
            i += 1
            continue

        m = re.match(r"^\[place:\s*([a-z0-9-]+)\s*(?:\|\s*(.*?))?\s*\]$", stripped, re.I)
        if m:
            flush_para(para); para = []
            slug, caption = m.group(1), (m.group(2) or "").strip()
            url = f"{PLACE_BASE}/listing/{slug}"
            blocks.append({
                "type": "place", "slug": slug, "base": PLACE_BASE, "url": url,
                "open_url": url, "style": "photo", "book_url": None,
                "book_label": "", "video": None, "caption": caption,
            })
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para(para); para = []
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            text = " ".join(quote)
            attribution = ""
            if "|" in text:
                text, attribution = [p.strip() for p in text.rsplit("|", 1)]
            blocks.append({"type": "quote", "text": text, "attribution": attribution})
            continue

        if BULLET.match(line) or NUMBER.match(line):
            flush_para(para); para = []
            numbered = bool(NUMBER.match(line))
            pattern = NUMBER if numbered else BULLET
            items = []
            while i < len(lines) and pattern.match(lines[i]):
                items.append(pattern.match(lines[i]).group(1).strip())
                i += 1
            blocks.append({"type": "list", "style": "numbered" if numbered else "bulleted", "items": items})
            continue

        para.append(line)
        i += 1

    flush_para(para)
    return blocks


def load_author(slug):
    path = ROOT / "content" / "authors" / f"{slug}.json"
    if not path.exists():
        sys.exit(f"No author file at content/authors/{slug}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("markdown")
    ap.add_argument("--publish", action="store_true", help="write as published instead of draft")
    ap.add_argument("--out", help="folder to write into (default: content/articles)")
    args = ap.parse_args()

    meta, body = split_front_matter(Path(args.markdown).read_text(encoding="utf-8"))

    title = meta.get("title")
    if not title:
        sys.exit("Add a title: line to the settings block.")
    category = meta.get("category", "")
    if category not in content.CATEGORIES:
        sys.exit(f"category must be one of: {', '.join(content.CATEGORIES)}")
    subcategory = meta.get("subcategory", "")
    if subcategory and subcategory not in content.CATEGORIES[category]["subcategories"]:
        sys.exit(f"subcategory for {category} must be one of: {', '.join(content.CATEGORIES[category]['subcategories'])}")

    author = load_author(meta.get("author", "daniel-mansfield"))
    blocks = parse_blocks(body)
    if not blocks:
        sys.exit("The story has no content under the settings block.")

    slug = meta.get("slug") or slugify(title)
    doc = {
        "slug": slug,
        "title": title,
        "dek": meta.get("dek", ""),
        "category": category,
        "subcategory": subcategory,
        "author": author["name"],
        "author_slug": author.get("slug") or meta.get("author", "daniel-mansfield"),
        "author_photo": author.get("photo"),
        "author_position": author.get("position"),
        "published_at": meta.get("date") or date.today().isoformat(),
        "cover_image": None,
        "featured": False,
        "status": "published" if args.publish else "draft",
        "blocks": blocks,
        "read_minutes": content.estimate_read_minutes(blocks),
    }

    out_dir = Path(args.out) if args.out else ROOT / "content" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug}.json"
    if out.exists():
        sys.exit(f"{out.name} already exists. Change the title/slug or delete the old file first.")
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    kinds = {}
    for b in blocks:
        kinds[b["type"]] = kinds.get(b["type"], 0) + 1
    print(f"Wrote {out}")
    print(f"Status: {doc['status']}  |  {doc['read_minutes']} min read  |  blocks: " +
          ", ".join(f"{n} {k}" for k, n in kinds.items()))


if __name__ == "__main__":
    main()
