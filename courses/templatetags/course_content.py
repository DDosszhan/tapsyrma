import re

from django import template
from django.templatetags.static import static
from django.utils.html import format_html, format_html_join


register = template.Library()

DIAGRAMS = {
    "place-values": ("Binary place values and the number 45", "courses/diagrams/place-values.svg"),
    "binary-operations": ("Eight-bit addition and overflow", "courses/diagrams/binary-operations.svg"),
    "sound-sampling": ("Sampling a sound wave at regular intervals", "courses/diagrams/sound-sampling.svg"),
    "pixel-grid": ("A small image represented by a grid of coloured pixels", "courses/diagrams/pixel-grid.svg"),
    "file-size": ("How to calculate uncompressed image and sound file sizes", "courses/diagrams/file-size.svg"),
    "compression": ("Run-length encoding compared with lossy compression", "courses/diagrams/compression.svg"),
}


@register.filter
def render_lesson_content(value):
    """Render a deliberately small, escaped lesson format with approved diagrams."""
    blocks = []
    for raw in re.split(r"\n\s*\n", (value or "").strip()):
        block = raw.strip()
        if not block:
            continue
        match = re.fullmatch(r"\[diagram:([a-z-]+)\]", block)
        if match and match.group(1) in DIAGRAMS:
            alt, path = DIAGRAMS[match.group(1)]
            blocks.append(format_html(
                '<figure class="lesson-diagram"><img src="{}" alt="{}" loading="lazy"></figure>',
                static(path), alt,
            ))
        elif block.startswith("## "):
            blocks.append(format_html("<h3>{}</h3>", block[3:]))
        elif block.startswith("### "):
            blocks.append(format_html("<h4>{}</h4>", block[4:]))
        elif block.startswith("Tip: "):
            blocks.append(format_html('<p class="lesson-tip"><strong>Tip</strong> {}</p>', block[5:]))
        else:
            lines = format_html_join("<br>", "{}", ((line,) for line in block.splitlines()))
            blocks.append(format_html("<p>{}</p>", lines))
    return format_html_join("", "{}", ((block,) for block in blocks))
