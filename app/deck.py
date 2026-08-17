"""Checking a poll file.

A poll file is JSON, because Claude writes it and a wrong guess should fail
loudly rather than quietly produce a poll with a missing option. Every problem
found is reported at once, with the question number, so a bad file takes one
round trip to fix rather than five.

    {
      "title": "MGMT 638 — Duration and convexity",
      "questions": [
        {"type": "choice", "text": "Which bond has the higher duration?",
         "options": ["The 5% coupon", "The zero"], "answer": 1},

        {"type": "wordcloud", "text": "One word: what is convexity good for?"},

        {"type": "scale", "text": "How solid do you feel about duration?",
         "min_label": "Lost", "max_label": "Solid"},

        {"type": "number", "text": "Guess the 10-year yield, in percent",
         "answer": 4.3, "unit": "%"},

        {"type": "rank", "text": "Order these by interest-rate risk",
         "options": ["T-bills", "Corporate bonds", "Long Treasuries"]}
      ]
    }

`answer` is optional. Where it is present the instructor gets a Reveal button
and the display marks the right one, which is what makes a question a concept
check rather than an opinion poll.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TYPES = ("choice", "wordcloud", "scale", "number", "rank")

MAX_OPTIONS = 10
MAX_QUESTIONS = 100


class DeckError(Exception):
    """Everything wrong with the file, in one message."""


def _check_choice(q: dict[str, Any], problems: list[str], where: str) -> None:
    options = q.get("options")
    if not isinstance(options, list) or len(options) < 2:
        problems.append(f"{where}: 'choice' needs an 'options' list with at least two entries")
        return
    if len(options) > MAX_OPTIONS:
        problems.append(f"{where}: {len(options)} options is too many to read on a projector (max {MAX_OPTIONS})")
    q["options"] = [str(o) for o in options]
    q["multi"] = bool(q.get("multi", False))

    if "answer" in q and q["answer"] is not None:
        answers = q["answer"] if isinstance(q["answer"], list) else [q["answer"]]
        for a in answers:
            if not isinstance(a, int) or not 0 <= a < len(options):
                problems.append(
                    f"{where}: 'answer' must be the 0-based index of an option "
                    f"(0 to {len(options) - 1}), not {a!r}"
                )
        q["answer"] = [a for a in answers if isinstance(a, int)]


def _check_rank(q: dict[str, Any], problems: list[str], where: str) -> None:
    options = q.get("options")
    if not isinstance(options, list) or len(options) < 2:
        problems.append(f"{where}: 'rank' needs an 'options' list with at least two entries")
        return
    if len(options) > 6:
        problems.append(f"{where}: ranking more than 6 things is a chore on a phone (got {len(options)})")
    q["options"] = [str(o) for o in options]


def _check_scale(q: dict[str, Any], problems: list[str], where: str) -> None:
    low = q.get("min", 1)
    high = q.get("max", 5)
    if not isinstance(low, int) or not isinstance(high, int) or high - low < 1:
        problems.append(f"{where}: 'scale' needs whole-number 'min' and 'max' with max above min")
        return
    if high - low > 10:
        problems.append(f"{where}: a {low}-{high} scale is too wide to tap; keep it to 10 points or fewer")
    q["min"], q["max"] = low, high
    q["min_label"] = str(q.get("min_label", ""))
    q["max_label"] = str(q.get("max_label", ""))


def _check_number(q: dict[str, Any], problems: list[str], where: str) -> None:
    if "answer" in q and q["answer"] is not None:
        if not isinstance(q["answer"], (int, float)):
            problems.append(f"{where}: 'answer' for a 'number' question must be a number")
    q["unit"] = str(q.get("unit", ""))


CHECKS = {
    "choice": _check_choice,
    "rank": _check_rank,
    "scale": _check_scale,
    "number": _check_number,
    "wordcloud": lambda q, problems, where: None,
}


def normalise(raw: Any) -> dict[str, Any]:
    """Validate a parsed poll file and fill in defaults. Raises DeckError."""
    if not isinstance(raw, dict):
        raise DeckError("The poll file must be a JSON object with a 'questions' list.")

    questions = raw.get("questions")
    if not isinstance(questions, list) or not questions:
        raise DeckError("The poll file needs a 'questions' list with at least one question.")
    if len(questions) > MAX_QUESTIONS:
        raise DeckError(f"{len(questions)} questions is more than one class can use (max {MAX_QUESTIONS}).")

    problems: list[str] = []
    clean: list[dict[str, Any]] = []

    for index, item in enumerate(questions):
        where = f"question {index + 1}"
        if not isinstance(item, dict):
            problems.append(f"{where}: should be an object, not {type(item).__name__}")
            continue

        q = dict(item)
        kind = q.get("type")
        if kind not in TYPES:
            problems.append(f"{where}: type {kind!r} is not one of {', '.join(TYPES)}")
            continue

        text = str(q.get("text", "")).strip()
        if not text:
            problems.append(f"{where}: needs a 'text' -- the question students read")
        q["text"] = text

        CHECKS[kind](q, problems, where)
        clean.append(q)

    if problems:
        raise DeckError("\n".join(problems))

    return {"title": str(raw.get("title", "")).strip(), "questions": clean}


def normalise_question(raw: Any) -> dict[str, Any]:
    """Validate one question on its own -- what `/poll <question>` produces.

    Same checks as a file, so a question typed mid-class cannot put something on
    the projector that a prepared file would have been refused for. Only the
    "question 1:" prefix goes, since there is no question 2 to tell it from.
    """
    try:
        return normalise({"questions": [raw]})["questions"][0]
    except DeckError as exc:
        cleaned = "\n".join(
            line.removeprefix("question 1: ") for line in str(exc).splitlines()
        )
        raise DeckError(cleaned) from None


def title_from_name(name: str) -> str:
    """A readable title from the filename the poll was written in.

    The client sends the stem rather than the path -- the server has no
    filesystem the instructor can see -- but a file called
    `class-4-duration.json` should still put "class 4 duration" on the
    projector when its author didn't write a title.
    """
    return Path(name).stem.replace("-", " ").replace("_", " ").strip()
