"""Input and grading text for the opt-in assembly trace table."""


TRACE_LAYOUT = "trace_table_v1"
TRACE_FIELDS = ("acc", "ix", "memory", "jump")


def is_trace_question(question):
    return question.kind == "open" and (question.interaction or {}).get("response_layout") == TRACE_LAYOUT


def read_trace_answer(post, question_id):
    """Read a complete form snapshot, retaining partially filled rows."""
    prefix = f"q_{question_id}_"
    allowed = {f"{prefix}trace_{row}_{field}" for row in range(3) for field in TRACE_FIELDS}
    allowed.update((f"{prefix}comparison", f"{prefix}reason"))
    if f"q_{question_id}" in post or any(key.startswith(prefix) and key not in allowed for key in post):
        raise ValueError("Invalid trace answer field.")

    def value(key, limit, *, multiline=False):
        items = post.getlist(key)
        if len(items) > 1:
            raise ValueError("Invalid trace answer field.")
        result = items[0].strip() if items else ""
        if len(result) > limit or (not multiline and ("\n" in result or "\r" in result)):
            raise ValueError("Trace answer is too long.")
        return result

    rows = []
    for row in range(3):
        entry = {field: value(f"{prefix}trace_{row}_{field}", 120) for field in TRACE_FIELDS}
        if entry["jump"] not in ("", "Yes", "No"):
            raise ValueError("Invalid jump answer.")
        rows.append(entry)
    comparison = value(f"{prefix}comparison", 120)
    reason = value(f"{prefix}reason", 5000, multiline=True)
    if sum(len(item) for row in rows for item in row.values()) + len(comparison) + len(reason) > 5000:
        raise ValueError("Answer is too long (maximum 5,000 characters).")
    if not any(item for row in rows for item in row.values()) and not comparison and not reason:
        return ""
    return {"version": 1, "rows": rows, "comparison": comparison, "reason": reason}


def format_trace_answer(answer):
    """Keep old free-text answers; give structured answers readable AI evidence."""
    if not isinstance(answer, dict):
        return answer
    rows = answer.get("rows")
    if (answer.get("version") != 1 or not isinstance(rows, list) or len(rows) != 3
            or not isinstance(answer.get("comparison"), str) or not isinstance(answer.get("reason"), str)):
        raise ValueError("Invalid trace answer.")
    lines = ["(a) Trace immediately after JPN LOOP:"]
    for number, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or any(not isinstance(row.get(field), str) for field in TRACE_FIELDS)
                or row["jump"] not in ("", "Yes", "No")):
            raise ValueError("Invalid trace answer row.")
        if any(row[field] for field in TRACE_FIELDS):
            lines.append(
                f"Iteration {number}: accumulator value = {row['acc'] or '(blank)'}; "
                f"index value = {row['ix'] or '(blank)'}; "
                f"memory address 400 contents = {row['memory'] or '(blank)'}; "
                f"JPN jumps = {row['jump'] or '(blank)'}")
        else:
            lines.append(f"Iteration {number}: blank")
    lines.extend(("(b) Compared value: " + (answer["comparison"] or "(blank)"),
                  "Why the program stops: " + (answer["reason"] or "(blank)")))
    return "\n".join(lines)
