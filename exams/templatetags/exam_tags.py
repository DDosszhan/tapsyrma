from itertools import groupby
import re

from django import template
from django.utils.html import escape, format_html, format_html_join

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


_MARKDOWN_TOKEN = re.compile(r"(\[\[\d+\]\]|\*\*[^*]+?\*\*|`[^`\n]+`)")
_BLANK = re.compile(r"\[\[(\d+)\]\]")
_FENCE = re.compile(r"^\s*```(?:text)?\s*$")


def _render_text(text, question, saved, result, *, formatting=True):
    """Escape author text while keeping the small markup used in fixed exams."""
    pieces = []
    for token in _MARKDOWN_TOKEN.split(text) if formatting else re.split(r"(\[\[\d+\]\])", text):
        blank = _BLANK.fullmatch(token)
        if blank and question.kind == "cloze":
            number = int(blank.group(1))
            if result:
                pieces.append(format_html('<span aria-label="Blank {}">____</span>', number))
            else:
                value = saved[number - 1] if isinstance(saved, list) and number <= len(saved) else ""
                pieces.append(format_html(
                    '<input type="text" name="q_{}_{}" maxlength="120" value="{}" aria-label="Blank {}">',
                    question.id, number - 1, value, number))
        elif formatting and token.startswith("**") and token.endswith("**"):
            pieces.append(format_html("<strong>{}</strong>",
                                      _render_text(token[2:-2], question, saved, result)))
        elif formatting and token.startswith("`") and token.endswith("`"):
            pieces.append(format_html("<code>{}</code>",
                                      _render_text(token[1:-1], question, saved, result, formatting=False)))
        else:
            pieces.append(escape(token))
    return format_html_join("", "{}", ((piece,) for piece in pieces))


def _stem_blocks(stem):
    lines = stem.splitlines()
    i = 0
    text_lines = []
    while i < len(lines):
        line = lines[i]
        fenced = bool(_FENCE.fullmatch(line))
        table = line.count("|") >= 2
        if not fenced and not table:
            text_lines.append(line)
            i += 1
            continue
        if text_lines:
            yield "text", "\n".join(text_lines).strip("\n")
            text_lines = []
        i += 1
        block = []
        if fenced:
            while i < len(lines) and not _FENCE.fullmatch(lines[i]):
                block.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            yield "code", "\n".join(block)
        else:
            block.append(line)
            while i < len(lines) and lines[i].count("|") >= 2:
                block.append(lines[i])
                i += 1
            cells = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in block]
            markdown = (len(cells) >= 2 and all(len(row) == len(cells[0]) for row in cells)
                        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells[1]))
            yield "table" if markdown else "plain_table", cells if markdown else "\n".join(block)
    if text_lines:
        yield "text", "\n".join(text_lines).strip("\n")


@register.simple_tag(takes_context=True)
def render_question_stem(context, question, result=False):
    saved = context.get("answers", {}).get(str(question.id), []) if not result else []
    cloze_class = " cloze-stem" if question.kind == "cloze" and not result else ""
    fixed_class = (" question-stem--fixed" if fixed_width_stem(question.stem)
                   and not any(line.count("|") >= 2 for line in question.stem.splitlines()) else "")
    output = []
    for kind, content in _stem_blocks(question.stem):
        if kind == "text" and content:
            output.append(format_html('<p class="question-stem{}{}">{}</p>', cloze_class, fixed_class,
                                      _render_text(content, question, saved, result)))
        elif kind == "code":
            output.append(format_html('<pre class="question-code{}"><code>{}</code></pre>', cloze_class,
                                      _render_text(content, question, saved, result, formatting=False)))
        elif kind == "plain_table":
            output.append(format_html(
                '<pre class="question-table-scroll{}" role="region" aria-label="Question table" tabindex="0">{}</pre>',
                cloze_class, _render_text(content, question, saved, result, formatting=False)))
            output.append(format_html('<p class="question-table-hint">{}</p>', "Scroll sideways to read the table."))
        elif kind == "table":
            header, _, *rows = content
            head = format_html_join("", '<th scope="col">{}</th>',
                                    ((_render_text(cell, question, saved, result),) for cell in header))
            body = format_html_join("", "<tr>{}</tr>",
                                    ((format_html_join("", "<td>{}</td>",
                                        ((_render_text(cell, question, saved, result),) for cell in row)),)
                                     for row in rows))
            output.append(format_html(
                '<div class="question-table-scroll{}" role="region" aria-label="Question table" tabindex="0"><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>',
                cloze_class, head, body))
            output.append(format_html('<p class="question-table-hint">{}</p>', "Scroll sideways to read the table."))
    return format_html_join("", "{}", ((part,) for part in output))
