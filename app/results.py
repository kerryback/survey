"""Handing the results back when the class is over.

One CSV per session, one row per answer option so it opens as something you can
read rather than nested JSON. Anonymous throughout: there is no student column
because there is no student data.

The server builds the text and names the file; it never writes one. It runs in a
container with no access to the instructor's disk, and the CSV has to arrive
either as a download in the browser or through the CLI into whatever folder the
instructor is working in.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

from . import tally as tally_mod

HEADER = ["question", "type", "responses", "item", "count", "share", "note"]


def rows_for(index: int, question: dict[str, Any], answers: list[Any]) -> list[list[Any]]:
    result = tally_mod.tally(question, answers)
    number = index + 1
    kind = question["type"]
    total = result["responses"]
    rows: list[list[Any]] = []

    def row(item: Any, count: Any = "", share: Any = "", note: str = "") -> None:
        rows.append([f"{number}. {question['text']}", kind, total, item, count, share, note])

    if kind == "choice":
        correct = set(result.get("answer") or [])
        for position, option in enumerate(result["options"]):
            row(option["text"], option["count"], round(option["share"], 4),
                "correct" if position in correct else "")
    elif kind == "wordcloud":
        for entry in result["words"]:
            row(entry["word"], entry["count"])
    elif kind == "scale":
        for point in result["points"]:
            row(point["value"], point["count"])
        row("mean", result["mean"])
    elif kind == "number":
        row("mean", result["mean"])
        row("median", result["median"])
        if result.get("answer") is not None:
            row("actual", result["answer"], "", "the right answer")
    elif kind == "rank":
        for position, entry in enumerate(result["rows"]):
            row(entry["text"], entry["average"], "", f"ranked {position + 1}")

    if not rows:  # a question nobody answered still deserves a line
        row("", 0, "", "no responses")
    return rows


def filename(session: Any, when: date | None = None) -> str:
    """What to call the CSV.

    A session built from a poll file is named after it, so re-running last
    term's questions produces a file that sorts next to the old one. A session
    of questions typed during class has no file to be named after.
    """
    deck = session.deck or {}
    # `name` is the poll file as the instructor knows it, extension and all.
    stem = Path(deck.get("name") or "survey").stem or "survey"
    return f"{stem}-results-{(when or date.today()).isoformat()}.csv"


def csv_text(session: Any) -> str:
    """The whole session as CSV. Raises ValueError if nothing is loaded."""
    deck = session.deck
    if deck is None:
        raise ValueError("No poll is loaded.")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    for index, question in enumerate(deck["questions"]):
        writer.writerows(rows_for(index, question, session.answers(index)))
    return buffer.getvalue()
