"""Hugo-markdown-aware chunker for kubernetes/website concept pages.

Pure logic, no I/O beyond the text handed in. Three Hugo realities drive the design:
frontmatter carries the title, shortcodes wrap prose that must survive stripping, and
code fences may contain ``{{ ... }}`` templating that must NOT be treated as shortcodes.
Markdown tables and code fences are atomic: they are never split across chunks, even
when that pushes a chunk past the size target (bge-small truncates at 512 tokens; an
oversized atomic block is accepted and logged by the caller rather than corrupted).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import frontmatter

# ~400 tokens, under bge-small's 512-token window with headroom for the title prefix.
MAX_CHUNK_CHARS = 1600

_IMAGE_PATTERNS = (
    re.compile(r"!\[[^\]]*\]\(([^)\s]+)"),
    re.compile(r"{{<\s*figure\b[^>]*src=\"([^\"]+)\""),
    re.compile(r"<img\b[^>]*src=[\"']([^\"']+)[\"']"),
)

_GLOSSARY_TEXT = re.compile(r"{{<\s*glossary_tooltip\b[^>]*?text=\"([^\"]+)\"[^>]*?>}}")
_GLOSSARY_TERM = re.compile(r"{{<\s*glossary_tooltip\b[^>]*?term_id=\"([^\"]+)\"[^>]*?>}}")
_FIGURE_CAPTION = re.compile(r"{{<\s*figure\b[^>]*?caption=\"([^\"]+)\"[^>]*?>}}")
# Any remaining {{< ... >}} or {{% ... %}} tag, opening or closing. Only the tag is
# removed; content between block tags (note, warning, caution) survives.
_SHORTCODE = re.compile(r"{{[<%][^>%]*?[>%]}}")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")


@dataclass
class Chunk:
    section: str
    index: int
    text: str


@dataclass
class Page:
    path: str
    title: str
    image_refs: list[str]
    chunks: list[Chunk]
    draft: bool = False


def find_image_refs(raw_markdown: str) -> list[str]:
    """Image references from RAW page source, before any stripping."""
    refs: list[str] = []
    for pattern in _IMAGE_PATTERNS:
        refs.extend(pattern.findall(raw_markdown))
    return refs


def _strip_hugo(prose: str) -> str:
    """Strip Hugo shortcodes from a NON-code-fence segment, keeping human text."""
    prose = _GLOSSARY_TEXT.sub(r"\1", prose)
    prose = _GLOSSARY_TERM.sub(r"\1", prose)
    prose = _FIGURE_CAPTION.sub(r"\1", prose)
    prose = _SHORTCODE.sub("", prose)
    prose = _HTML_COMMENT.sub("", prose)
    return prose


def _strip_outside_fences(content: str) -> str:
    """Apply shortcode stripping only outside ``` fences, preserving templating inside."""
    out_lines: list[str] = []
    prose: list[str] = []
    in_fence = False

    def flush_prose() -> None:
        if prose:
            out_lines.append(_strip_hugo("\n".join(prose)))
            prose.clear()

    for line in content.split("\n"):
        if _FENCE.match(line):
            if not in_fence:
                flush_prose()
            out_lines.append(line)
            in_fence = not in_fence
            continue
        if in_fence:
            out_lines.append(line)
        else:
            prose.append(line)
    flush_prose()
    return "\n".join(out_lines)


def _blocks(section_text: str) -> list[str]:
    """Split section prose into atomic blocks: paragraphs, whole tables, whole fences."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current.clear()

    for line in section_text.split("\n"):
        if _FENCE.match(line):
            if not in_fence:
                flush()
            current.append(line)
            if in_fence:
                flush()
            in_fence = not in_fence
            continue
        if in_fence:
            current.append(line)
            continue
        if line.strip() == "":
            flush()
            continue
        is_table_row = line.lstrip().startswith("|")
        if current and is_table_row != current[-1].lstrip().startswith("|"):
            flush()
        current.append(line)
    flush()
    return blocks


def _sections(content: str) -> list[tuple[str, str]]:
    """Split content on headings into (heading_trail, text) pairs."""
    sections: list[tuple[str, str]] = []
    h2 = ""
    h3 = ""
    lines: list[str] = []
    trail = ""
    in_fence = False

    def flush() -> None:
        nonlocal lines
        text = "\n".join(lines).strip()
        if text:
            sections.append((trail, text))
        lines = []

    for line in content.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            lines.append(line)
            continue
        flush()
        level = len(match.group(1))
        heading = match.group(2).strip()
        if level <= 2:
            h2, h3 = heading, ""
        else:
            h3 = heading
        trail = f"{h2} / {h3}" if h2 and h3 else (h3 or h2)
    flush()
    return sections


def _pack(blocks: list[str], max_chars: int) -> list[str]:
    """Greedy-pack blocks into chunks; an oversized atomic block becomes its own chunk."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_page(path: str, raw_markdown: str, max_chars: int = MAX_CHUNK_CHARS) -> Page:
    """Parse one Hugo markdown page into an ordered list of embeddable chunks."""
    post = frontmatter.loads(raw_markdown)
    title = str(post.get("title") or path)
    draft = bool(post.get("draft", False))
    image_refs = find_image_refs(post.content)
    content = _strip_outside_fences(post.content)

    chunks: list[Chunk] = []
    index = 0
    for section, text in _sections(content):
        for body in _pack(_blocks(text), max_chars):
            prefix = f"{title} / {section}" if section else title
            chunks.append(Chunk(section=section, index=index, text=f"{prefix}\n\n{body}"))
            index += 1
    return Page(path=path, title=title, image_refs=image_refs, chunks=chunks, draft=draft)
