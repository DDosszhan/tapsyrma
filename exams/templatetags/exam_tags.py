from itertools import groupby
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
def lettered_matching(options):
    return (isinstance(options, list) and 2 <= len(options) <= 26
            and all(isinstance(option, str) and option.startswith(f"{chr(65 + index)}. ")
                    for index, option in enumerate(options)))


@register.filter
def fixed_width_stem(stem):
    return isinstance(stem, str) and (any(line.count("|") >= 2 for line in stem.splitlines())
                                      or ("<-" in stem and "[[" in stem))


@register.filter
def table_stem_parts(stem):
    if not isinstance(stem, str):
        return []
    lines = stem.splitlines(keepends=True)
    has_table = any(line.count("|") >= 2 for line in lines)
    fixed = not has_table and fixed_width_stem(stem)
    parts = []
    for is_table, group in groupby(lines, key=lambda line: line.count("|") >= 2):
        text = "".join(group).strip("\r\n")
        if text:
            parts.append({"table": is_table, "text": text, "fixed": fixed})
    return parts


@register.filter
def cloze_parts(stem):
    parts = []
    for part in re.split(r"(\[\[\d+\]\])", stem):
        match = re.fullmatch(r"\[\[(\d+)\]\]", part)
        parts.append({"blank": True, "index": int(match.group(1)) - 1, "number": match.group(1)} if match
                     else {"blank": False, "text": part})
    return parts
