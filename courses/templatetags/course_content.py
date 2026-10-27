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

DIAGRAMS.update({
    slug: (alt, f"courses/diagrams/hardware/{slug}.svg")
    for slug, alt in {
        "cpu-units": "Main memory connected to the CPU, showing the control unit and arithmetic logic unit.",
        "von-neumann": "Instructions and data stored together in main memory for the CPU to fetch.",
        "register-map": "The five Cambridge CPU registers with their roles.",
        "bus-map": "Direction and purpose of address, data and control buses.",
        "fde-cycle": "Three stages of the fetch-decode-execute cycle with relevant registers.",
        "performance": "Clock, cache and cores as distinct CPU performance factors.",
        "input-families": "Examples of input devices grouped by the data they capture.",
        "touch-types": "Resistive, capacitive and infrared touchscreen detection methods.",
        "control-loop": "Flow of information from a sensor to a controller and actuator.",
        "output-families": "Common output devices and the kinds of outputs they produce.",
        "memory-hierarchy": "Relationship between CPU, primary storage and secondary storage.",
        "volatile": "Effect of power loss on RAM, ROM and SSD storage.",
        "three-storage": "Distinct physical mechanisms of HDD, optical disc and SSD.",
        "storage-choice": "Factors to compare when selecting a storage medium.",
        "paging": "Memory pages moving between RAM and secondary storage.",
        "cloud": "Two devices accessing data on a physical remote cloud server.",
        "network-path": "A device accessing a remote server through a network interface and router.",
        "address-types": "Examples of correctly formatted MAC, IPv4 and IPv6 addresses.",
    }.items()
})


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
