"""Inline links inside story text.

A writer links some words by typing  [the words](https://address)  in a
paragraph, list item, tip or takeaway (the story editor's Link button does
this for you). Everything else in the text stays plain, and is escaped, so
nobody can sneak HTML into a story. Only http and https addresses become
links, and they open in a new tab.

The same rule is written again in JavaScript (templates/base.html,
inlineLinks) for the pop-up story sheet. Keep the two in step.
"""
import re

from markupsafe import Markup, escape

LINK_RE = re.compile(r"\[([^\[\]]{1,300})\]\((https?://[^\s()<>\[\]\"']{1,2000})\)")


def inline_links(text):
    """Escape text and turn [words](https://...) into an underlined link."""
    text = "" if text is None else str(text)
    out = []
    pos = 0
    for m in LINK_RE.finditer(text):
        out.append(escape(text[pos:m.start()]))
        out.append(Markup('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>') % (m.group(2), m.group(1)))
        pos = m.end()
    out.append(escape(text[pos:]))
    return Markup("").join(out)


def plain_text(text):
    """The words only: [words](https://...) becomes words (for read time and search)."""
    return LINK_RE.sub(lambda m: m.group(1), "" if text is None else str(text))
