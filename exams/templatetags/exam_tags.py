import re

from django import template

register = template.Library()


@register.filter
def dict_get(mapping, key):
    return mapping.get(str(key), "")


@register.filter
def list_get(values, index):
    return values[index] if isinstance(values, list) and isinstance(index, int) and 0 <= index < len(values) else ""


@register.filter
def has_index(values, index):
    return isinstance(values, list) and str(index) in values


@register.filter
def fixed_width_stem(stem):
    return isinstance(stem, str) and (any(line.count("|") >= 2 for line in stem.splitlines())
                                      or ("<-" in stem and "[[" in stem))


@register.filter
def cloze_parts(stem):
    parts = []
    for part in re.split(r"(\[\[\d+\]\])", stem):
        match = re.fullmatch(r"\[\[(\d+)\]\]", part)
        parts.append({"blank": True, "index": int(match.group(1)) - 1, "number": match.group(1)} if match
                     else {"blank": False, "text": part})
    return parts
