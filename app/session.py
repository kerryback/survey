"""The live state of a poll: what is on screen, who has answered, and the tally.

One classroom, one session. Nothing is persisted at all -- not while the class
runs and not after it. The projector is the shared artifact and the CSV is
pulled down at the end. If the container restarts, the class is gone; that is
the accepted cost of having no database to keep running.

Two rules here are pedagogy rather than plumbing:

Answers are keyed by an anonymous per-browser id, so a student can change their
mind while voting is open without being counted twice, and nothing identifies
anybody.

A question with a right answer hides its tally while voting is open. Watching a
bar grow tells the room what the popular answer is, and quiet students follow
it. Opinion questions have nothing to bias, so those show live.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from . import tally as tally_mod

MAX_TEXT = 200


class Session:
    def __init__(self, code: str, student_url: str = "") -> None:
        self.code = code
        self.student_url = student_url
        # Minted per session, not per process. It goes in the /display URL, so a
        # key read off the projector's address bar is dead the moment the class
        # ends rather than good for every class after it.
        self.display_key = secrets.token_urlsafe(12)
        self.deck: dict[str, Any] | None = None
        self.index = -1  # -1 is the title screen, before the first question
        self.open = False
        self.show_results = True
        self.revealed = False
        self.responses: list[dict[str, Any]] = []
        self.participants: set[str] = set()
        self.displays: set[Any] = set()
        self.voters: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # --- deck -------------------------------------------------------------

    def extend(
        self,
        questions: list[dict[str, Any]],
        title: str = "",
        name: str = "",
    ) -> int:
        """Append questions to the session and return the index of the first.

        Everything appends -- a prepared file and a question typed mid-class go
        into the same growing list. One class is one session, so nothing the
        instructor types can wipe out answers already given, and the CSV at the
        end covers the whole meeting rather than whichever file was loaded last.
        """
        if self.deck is None:
            self.deck = {"title": title, "questions": [], "name": name}
        first = len(self.deck["questions"])
        self.deck["questions"].extend(questions)
        self.responses.extend({} for _ in questions)
        # The first file to arrive names the session and the CSV; later ones join
        # it rather than renaming it underneath.
        if title and not self.deck["title"]:
            self.deck["title"] = title
        if name and not self.deck.get("name"):
            self.deck["name"] = name
        return first

    def reset(self) -> None:
        """Throw the session away. Only ever on the instructor's say-so."""
        self.deck = None
        self.responses = []
        self.index = -1
        self.open = False
        self.revealed = False
        self.show_results = True

    @property
    def question(self) -> dict[str, Any] | None:
        if self.deck is None or not 0 <= self.index < len(self.deck["questions"]):
            return None
        return self.deck["questions"][self.index]

    def _has_answer(self, question: dict[str, Any]) -> bool:
        return question.get("answer") not in (None, [], "")

    # --- running the poll -------------------------------------------------

    async def goto(self, index: int) -> None:
        if self.deck is None:
            return
        self.index = max(-1, min(index, len(self.deck["questions"]) - 1))
        question = self.question
        self.revealed = False
        # Voting opens with the question. The instructor never has to click
        # twice to get a question working, only to stop it.
        self.open = question is not None
        self.show_results = not (question is not None and self._has_answer(question))
        await self.broadcast()

    async def set_open(self, is_open: bool) -> None:
        if self.question is None:
            return
        self.open = is_open
        if not is_open:
            self.show_results = True  # closing voting is what "show me" means
        await self.broadcast()

    async def set_show_results(self, show: bool) -> None:
        self.show_results = show
        await self.broadcast()

    async def set_revealed(self, revealed: bool) -> None:
        question = self.question
        if question is None or not self._has_answer(question):
            return
        self.revealed = revealed
        if revealed:
            self.open = False
            self.show_results = True
        await self.broadcast()

    # --- answers ----------------------------------------------------------

    def check(self, question: dict[str, Any], value: Any) -> tuple[bool, Any]:
        """Coerce a submitted answer, or reject it."""
        kind = question["type"]

        if kind == "choice":
            options = question["options"]
            if question.get("multi"):
                if not isinstance(value, list):
                    return False, None
                picked = sorted({v for v in value if isinstance(v, int) and 0 <= v < len(options)})
                return (bool(picked), picked)
            return (isinstance(value, int) and 0 <= value < len(options)), value

        if kind == "wordcloud":
            text = str(value or "").strip()[:MAX_TEXT]
            return bool(text), text

        if kind == "scale":
            ok = isinstance(value, (int, float)) and question["min"] <= value <= question["max"]
            return ok, int(value) if ok else None

        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, None
            return True, float(value)

        if kind == "rank":
            size = len(question["options"])
            ok = isinstance(value, list) and sorted(value) == list(range(size))
            return ok, list(value) if ok else None

        return False, None

    async def record(self, participant: str, index: int, value: Any) -> tuple[bool, str]:
        if self.deck is None or index != self.index:
            return False, "That question is no longer on screen."
        if not self.open:
            return False, "Voting is closed."
        question = self.question
        if question is None:
            return False, "Nothing is open."

        ok, cleaned = self.check(question, value)
        if not ok:
            return False, "That answer doesn't fit the question."

        self.responses[index][participant] = cleaned
        await self.broadcast()
        return True, ""

    def answers(self, index: int) -> list[Any]:
        if 0 <= index < len(self.responses):
            return list(self.responses[index].values())
        return []

    # --- what each end sees ------------------------------------------------

    def display_state(self) -> dict[str, Any]:
        question = self.question
        state: dict[str, Any] = {
            "type": "state",
            "code": self.code,
            "student_url": self.student_url,
            "title": self.deck["title"] if self.deck else "",
            "loaded": self.deck is not None,
            "count": len(self.deck["questions"]) if self.deck else 0,
            "index": self.index,
            "open": self.open,
            "show_results": self.show_results,
            "revealed": self.revealed,
            "here": len(self.participants),
            "question": None,
            "results": None,
            "responses": len(self.responses[self.index]) if question else 0,
            "can_reveal": bool(question and self._has_answer(question)),
        }
        # The whole list travels with every state message, so the instructor can
        # open the menu and jump to any question at any time. A prepared class is
        # a set of questions to reach for, not a queue to walk in order.
        state["menu"] = [
            {
                "index": position,
                "type": item["type"],
                "text": item["text"],
                "responses": len(self.responses[position]),
                "asked": bool(self.responses[position]) or position == self.index,
            }
            for position, item in enumerate(self.deck["questions"])
        ] if self.deck else []

        if question:
            state["question"] = question
            if self.show_results:
                state["results"] = tally_mod.tally(question, self.answers(self.index))
        return state

    def voter_state(self, participant: str) -> dict[str, Any]:
        """Students get the question and their own answer -- never the tally.

        The projector is where the room looks together; sending the running
        count to forty phones would both bias the vote and make the shared
        screen pointless.
        """
        question = self.question
        answered = question is not None and participant in self.responses[self.index]
        state: dict[str, Any] = {
            "type": "state",
            "index": self.index,
            "open": self.open,
            "revealed": self.revealed,
            "question": question,
            "answered": answered,
            "mine": self.responses[self.index].get(participant) if question else None,
            "answer": question.get("answer") if (question and self.revealed) else None,
        }
        return state

    # --- sockets ----------------------------------------------------------

    async def broadcast(self) -> None:
        state = self.display_state()
        for ws in list(self.displays):
            await self._send(ws, state, self.displays)
        for participant, ws in list(self.voters.items()):
            await self._send(ws, self.voter_state(participant))

    async def _send(self, ws: Any, message: dict[str, Any], bucket: set[Any] | None = None) -> None:
        try:
            await ws.send_json(message)
        except Exception:
            if bucket is not None:
                bucket.discard(ws)

    async def add_display(self, ws: Any) -> None:
        self.displays.add(ws)
        await self._send(ws, self.display_state(), self.displays)

    async def add_voter(self, participant: str, ws: Any) -> None:
        async with self._lock:
            self.voters[participant] = ws
            self.participants.add(participant)
        await self._send(ws, self.voter_state(participant))
        await self.broadcast()

    async def remove_voter(self, participant: str, ws: Any) -> None:
        async with self._lock:
            if self.voters.get(participant) is ws:
                self.voters.pop(participant, None)
        await self.broadcast()

    async def close(self) -> None:
        """Turn the room's lights off: hang up on every display and phone.

        Without this, a projector page and forty phones would sit reconnecting
        to a session that no longer exists.
        """
        for ws in list(self.displays) + list(self.voters.values()):
            try:
                await ws.close(code=4403)
            except Exception:
                pass
        self.displays.clear()
        self.voters.clear()
