"""Turning raw answers into what goes on the projector.

Each question type aggregates differently, and each returns a shape the display
can draw without doing arithmetic of its own. Everything here is pure: given the
question and the answers, the result is the same every time.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Words that would otherwise dominate every word cloud from an English class.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does",
    "for", "from", "had", "has", "have", "how", "i", "if", "in", "is", "it",
    "its", "just", "me", "more", "my", "no", "not", "of", "on", "or", "our",
    "so", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "to", "too", "up", "very", "was", "we", "were", "what",
    "when", "which", "who", "why", "will", "with", "would", "you", "your",
}

WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)

MAX_CLOUD_WORDS = 40
# Up to this many words counts as one answer rather than several.
MAX_PHRASE_WORDS = 3
HISTOGRAM_BINS = 12


def _words(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text or "")]


def _trim(words: list[str]) -> list[str]:
    """Drop stopwords from the ends, keep the ones in the middle.

    "the duration" should land on the same entry as "duration", but "cost of
    capital" is not improved by losing its "of".
    """
    start, end = 0, len(words)
    while start < end and words[start] in STOPWORDS:
        start += 1
    while end > start and words[end - 1] in STOPWORDS:
        end -= 1
    return words[start:end]


def _wordcloud(answers: list[Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for answer in answers:
        words = _trim(_words(str(answer)))
        # "risk risk risk" is one student being emphatic, not a three-word
        # answer, and it should land on the same entry as a plain "risk".
        words = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
        if not words:
            continue

        if len(words) <= MAX_PHRASE_WORDS:
            # Keep a short answer whole. "interest rate risk" is one idea, and
            # splitting it puts a bare "risk" on the screen next to everyone
            # else's -- the cloud gets tidier and says less.
            counts[" ".join(words)] += 1
            continue

        # Past a few words it is a sentence, which would match nobody else and
        # would draw as an unreadable ribbon. Fall back to its content words.
        seen = set()
        for word in words:
            # One student saying "risk risk risk" should count once, or a single
            # enthusiastic response skews the whole cloud.
            if len(word) < 2 or word in STOPWORDS or word in seen:
                continue
            seen.add(word)
            counts[word] += 1

    common = counts.most_common(MAX_CLOUD_WORDS)
    top = common[0][1] if common else 1
    return {
        "words": [{"word": w, "count": c, "weight": c / top} for w, c in common],
        "distinct": len(counts),
    }


def _choice(question: dict[str, Any], answers: list[Any]) -> dict[str, Any]:
    options = question["options"]
    counts = [0] * len(options)
    for answer in answers:
        picked = answer if isinstance(answer, list) else [answer]
        for value in picked:
            if isinstance(value, int) and 0 <= value < len(options):
                counts[value] += 1

    total = sum(counts) or 1
    return {
        "options": [
            {"text": text, "count": count, "share": count / total}
            for text, count in zip(options, counts)
        ],
        "answer": question.get("answer") or [],
    }


def _scale(question: dict[str, Any], answers: list[Any]) -> dict[str, Any]:
    low, high = question["min"], question["max"]
    counts = {value: 0 for value in range(low, high + 1)}
    values: list[float] = []
    for answer in answers:
        if isinstance(answer, (int, float)) and low <= answer <= high:
            counts[int(answer)] += 1
            values.append(float(answer))

    top = max(counts.values()) or 1
    return {
        "points": [
            {"value": value, "count": count, "weight": count / top}
            for value, count in sorted(counts.items())
        ],
        "mean": round(sum(values) / len(values), 2) if values else None,
        "min_label": question.get("min_label", ""),
        "max_label": question.get("max_label", ""),
    }


def _number(question: dict[str, Any], answers: list[Any]) -> dict[str, Any]:
    values = sorted(float(a) for a in answers if isinstance(a, (int, float)))
    if not values:
        return {"bins": [], "mean": None, "median": None, "count": 0,
                "answer": question.get("answer"), "unit": question.get("unit", "")}

    low, high = values[0], values[-1]
    if high == low:  # everyone said the same thing; one bar, not a divide by zero
        bins = [{"start": low, "end": low, "count": len(values), "weight": 1.0}]
    else:
        width = (high - low) / HISTOGRAM_BINS
        counts = [0] * HISTOGRAM_BINS
        for value in values:
            index = min(int((value - low) / width), HISTOGRAM_BINS - 1)
            counts[index] += 1
        top = max(counts) or 1
        bins = [
            {"start": low + i * width, "end": low + (i + 1) * width,
             "count": count, "weight": count / top}
            for i, count in enumerate(counts)
        ]

    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {
        "bins": bins,
        "mean": round(sum(values) / len(values), 3),
        "median": round(median, 3),
        "count": len(values),
        "answer": question.get("answer"),
        "unit": question.get("unit", ""),
    }


def _rank(question: dict[str, Any], answers: list[Any]) -> dict[str, Any]:
    options = question["options"]
    totals = [0.0] * len(options)
    counted = [0] * len(options)

    for answer in answers:
        if not isinstance(answer, list) or sorted(answer) != list(range(len(options))):
            continue  # a partial or malformed ordering tells us nothing
        for position, option_index in enumerate(answer):
            totals[option_index] += position + 1
            counted[option_index] += 1

    rows = [
        {
            "text": options[i],
            "average": round(totals[i] / counted[i], 2) if counted[i] else None,
        }
        for i in range(len(options))
    ]
    # Lowest average position first -- that is what "ranked highest" means here.
    rows.sort(key=lambda r: (r["average"] is None, r["average"]))
    return {"rows": rows, "complete": sum(1 for a in answers if isinstance(a, list))}


def tally(question: dict[str, Any], answers: list[Any]) -> dict[str, Any]:
    kind = question["type"]
    if kind == "wordcloud":
        result = _wordcloud(answers)
    elif kind == "choice":
        result = _choice(question, answers)
    elif kind == "scale":
        result = _scale(question, answers)
    elif kind == "number":
        result = _number(question, answers)
    elif kind == "rank":
        result = _rank(question, answers)
    else:
        result = {}
    result["responses"] = len(answers)
    return result
