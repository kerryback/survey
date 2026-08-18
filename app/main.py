"""Live in-class polling, at a fixed public address.

The instructor drives a full-screen display on the classroom computer; students
answer from their own phones at https://poll.kerryback.com. Answers are
anonymous: there is no name field, no roster, and nothing stored that could
identify who said what.

This is the hosted successor to the `poll` skill, which published a local server
through a Cloudflare Quick Tunnel. The tunnel was the weak point -- ephemeral
random hostnames on a domain that campus filters have good reason to block, plus
a cold start every class. A permanent hostname on a domain the instructor owns
has neither problem.

One session at a time, held in memory. Nothing is persisted, so a redeploy or an
instance restart loses the class -- the CSV is the only durable artifact and the
instructor pulls it down at the end.

Two credentials, neither of them a login:

  SURVEY_TOKEN   the instructor's, sent as a bearer header by the CLI. It never
                 appears in a URL, so it cannot leak through browser history or
                 a screen-shared address bar.
  display_key    minted per session, in the /display URL. Scoped to the session
                 so that a key read off the projector dies with the class.

Students are gated by the room code, which is on the projector and not in the
URL -- enough that a forwarded link doesn't let the internet vote.
"""

from __future__ import annotations

import io
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

import segno
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import deck as deck_mod, results, tally
from .session import Session

BASE_DIR = Path(__file__).resolve().parent

PUBLIC_URL = (os.environ.get("PUBLIC_URL") or "").rstrip("/")
TOKEN = os.environ.get("SURVEY_TOKEN") or ""
FIXED_CODE = (os.environ.get("SURVEY_CODE") or "").strip()

# One classroom at a time. None until the instructor starts one, which is what
# makes the student page say "nothing is running" instead of offering a code box
# to anybody who finds the address.
SESSION: Session | None = None

app = FastAPI(title="Survey")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def room_code() -> str:
    """A fresh code per session unless one is pinned.

    Fresh is the default because the address is permanent: a code that leaked
    last week must not still open the door this week. SURVEY_CODE pins it for an
    instructor who would rather say the same four digits every time.
    """
    return FIXED_CODE or f"{secrets.randbelow(9000) + 1000}"


def student_url(code: str = "") -> str:
    base = PUBLIC_URL or "http://127.0.0.1:8000"
    return f"{base}/?c={quote(code)}" if code else base


# --- credentials -------------------------------------------------------------


def _require_token(authorization: str | None) -> None:
    """The instructor's routes. 404 rather than 401, so a scan of this host
    cannot tell an unguessed token from a URL that was never there."""
    given = ""
    if authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    if not TOKEN or not secrets.compare_digest(given, TOKEN):
        raise HTTPException(status_code=404, detail="Not found")


def _require_display_key(key: str) -> Session:
    """The projector page. Returns the session it belongs to."""
    live = SESSION
    if live is None or not secrets.compare_digest(key or "", live.display_key):
        raise HTTPException(status_code=404, detail="Not found")
    return live


def _live() -> Session:
    """The session, for routes that need one to already exist."""
    if SESSION is None:
        raise HTTPException(status_code=409, detail="No session is running.")
    return SESSION


def _ensure() -> Session:
    """The session, starting one if there isn't one.

    `ask` and `file` both come through here, so the first command of a class is
    one command -- the instructor says the question out loud and it goes up,
    without a separate start first.
    """
    global SESSION
    if SESSION is None:
        code = room_code()
        SESSION = Session(code, student_url(code))
    return SESSION


def _facts(live: Session) -> dict[str, Any]:
    """What the instructor needs to get the room in: where to go, what to type,
    and where the controls are."""
    return {
        "student_url": live.student_url,
        "room_code": live.code,
        "display_url": f"{PUBLIC_URL or ''}/display?key={live.display_key}",
        "display_key": live.display_key,
    }


# --- pages -----------------------------------------------------------------


@app.get("/")
def vote_page(request: Request):
    return templates.TemplateResponse(
        request, "vote.html", {"running": SESSION is not None}
    )


@app.get("/display")
def display_page(request: Request, key: str = ""):
    live = _require_display_key(key)
    return templates.TemplateResponse(
        request, "display.html", {"key": live.display_key, "code": live.code}
    )


@app.get("/healthz")
def healthz():
    return {"ok": True, "running": SESSION is not None}


# --- the session -----------------------------------------------------------


@app.post("/api/session")
async def start_session(authorization: str | None = Header(default=None)):
    """Bring the room up. Idempotent: an existing session is adopted, not
    replaced, so a second `start` mid-class cannot discard answers."""
    _require_token(authorization)
    existed = SESSION is not None
    live = _ensure()
    return {
        "started": not existed,
        **_facts(live),
        "questions": len(live.deck["questions"]) if live.deck else 0,
        "here": len(live.participants),
    }


@app.post("/api/reset")
async def reset(authorization: str | None = Header(default=None)):
    """Empty the session, keeping the room open on the same code and link.
    Explicit only -- nothing else here discards answers."""
    _require_token(authorization)
    live = _live()
    live.reset()
    await live.broadcast()
    return {"ok": True, **_facts(live)}


@app.post("/api/stop")
async def stop(authorization: str | None = Header(default=None)):
    """End the class. The code stops working and the display key goes dead."""
    global SESSION
    _require_token(authorization)
    live = SESSION
    if live is None:
        return {"ok": True, "running": False}
    await live.close()
    SESSION = None
    return {"ok": True, "running": False, "stopped": True}


# --- questions -------------------------------------------------------------


@app.post("/api/deck")
async def add_deck(payload: dict, authorization: str | None = Header(default=None)):
    """Add a prepared poll file to the session.

    The client sends the file's contents, not a path -- this server has no
    access to the instructor's disk. The questions append and the pointer does
    not move: loading a file at the top of class leaves the join screen up, and
    loading one mid-class doesn't yank the projector off the question the room
    is answering.

    A bad file is rejected with every problem listed at once and the session is
    left exactly as it was, so a typo mid-class cannot disturb the questions
    that are working.
    """
    _require_token(authorization)
    raw = payload.get("deck") if "deck" in payload else payload
    try:
        loaded = deck_mod.normalise(raw)
    except deck_mod.DeckError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    name = str(payload.get("name") or "").strip()
    title = loaded["title"] or deck_mod.title_from_name(name)

    live = _ensure()
    first = live.extend(loaded["questions"], title, name)
    await live.broadcast()
    return {
        "title": live.deck["title"],
        "added": len(loaded["questions"]),
        "first": first,
        "total": len(live.deck["questions"]),
        "types": [q["type"] for q in loaded["questions"]],
        **_facts(live),
    }


@app.post("/api/question")
async def add_question(payload: dict, authorization: str | None = Header(default=None)):
    """Put one question on the projector now.

    Unlike a file, this jumps straight to what it just added and opens voting.
    That is the whole point: the instructor said the question out loud ten
    seconds ago and the room is waiting.
    """
    _require_token(authorization)
    raw = payload.get("question") if "question" in payload else payload
    try:
        question = deck_mod.normalise_question(raw)
    except deck_mod.DeckError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    live = _ensure()
    index = live.extend([question])
    await live.goto(index)
    return {
        "index": index,
        "total": len(live.deck["questions"]),
        "type": question["type"],
        "text": question["text"],
        **_facts(live),
    }


@app.post("/api/validate")
async def validate(payload: dict, authorization: str | None = Header(default=None)):
    """Check a poll file without loading it, so it can be fixed before class.

    Validation lives here rather than in the CLI so the schema has exactly one
    implementation -- a client-side copy would drift from what the server
    actually accepts, and would be wrong in the worst place: in front of a room.
    """
    _require_token(authorization)
    raw = payload.get("deck") if "deck" in payload else payload
    try:
        loaded = deck_mod.normalise(raw)
    except deck_mod.DeckError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "title": loaded["title"] or deck_mod.title_from_name(str(payload.get("name") or "")),
        "questions": len(loaded["questions"]),
        "types": [q["type"] for q in loaded["questions"]],
        "with_answers": sum(1 for q in loaded["questions"] if q.get("answer") not in (None, [], "")),
    }


# --- reading it back -------------------------------------------------------


@app.get("/api/state")
def state(authorization: str | None = Header(default=None)):
    """Everything about the session, including every tally so far."""
    _require_token(authorization)
    if SESSION is None:
        return JSONResponse({"running": False})
    snapshot = SESSION.display_state()
    snapshot["running"] = True
    snapshot.update(_facts(SESSION))
    if SESSION.deck is not None:
        snapshot["all_results"] = [
            dict(tally.tally(question, SESSION.answers(index)), question=question["text"])
            for index, question in enumerate(SESSION.deck["questions"])
        ]
    return JSONResponse(snapshot)


@app.get("/api/results.csv")
def results_csv(authorization: str | None = Header(default=None), key: str = ""):
    """The session as CSV.

    Two ways in, because there are two hands that want it: the CLI carries the
    bearer token, and the projector's save button can only offer a link, so it
    passes the display key instead.
    """
    if key:
        _require_display_key(key)
    else:
        _require_token(authorization)
    live = _live()
    try:
        body = results.csv_text(live)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return Response(
        body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{results.filename(live)}"'
        },
    )


@app.get("/api/qr.svg")
def qr(key: str = ""):
    """The join link as a QR code, with the room code in it.

    Scanning it puts the code in the box; the student still taps Join. The code
    is on the projector beside the QR anyway, so carrying it costs no secrecy
    and saves forty people typing four digits.
    """
    live = _require_display_key(key)
    buffer = io.BytesIO()
    segno.make(live.student_url, error="m").save(
        buffer, kind="svg", scale=5, border=2, dark="#0f172a", light="#ffffff"
    )
    return Response(buffer.getvalue(), media_type="image/svg+xml")


# --- live ------------------------------------------------------------------


@app.websocket("/ws/display")
async def ws_display(ws: WebSocket, key: str = ""):
    live = SESSION
    if live is None or not secrets.compare_digest(key or "", live.display_key):
        # Accept and then close, rather than refusing the handshake. A refused
        # handshake reaches the browser as a generic 1006, indistinguishable
        # from classroom wifi dropping -- and the page would reconnect forever.
        # Accepting first is what lets us hang up with 4403, which the display
        # reads as "this session is over" and says so instead of retrying.
        await ws.accept()
        await ws.close(code=4403)
        return
    await ws.accept()
    await live.add_display(ws)
    try:
        while True:
            message = await ws.receive_json()
            kind = message.get("type")
            if kind == "goto":
                await live.goto(int(message.get("index", -1)))
            elif kind == "open":
                await live.set_open(bool(message.get("on")))
            elif kind == "results":
                await live.set_show_results(bool(message.get("on")))
            elif kind == "reveal":
                await live.set_revealed(bool(message.get("on")))
    except (WebSocketDisconnect, RuntimeError, ValueError, TypeError):
        pass
    finally:
        live.displays.discard(ws)


@app.websocket("/ws/vote")
async def ws_vote(ws: WebSocket):
    await ws.accept()
    live = SESSION
    if live is None:
        # The address is permanent, so people will arrive when no class is on.
        # Say so plainly instead of asking them for a code that cannot exist.
        await ws.send_json({"type": "error", "message": "No poll is running right now."})
        await ws.close()
        return

    participant = ""
    try:
        first = await ws.receive_json()
        given = str(first.get("code") or "").strip()
        if first.get("type") != "join" or not secrets.compare_digest(given, live.code):
            await ws.send_json(
                {"type": "error", "message": "That code doesn't match the one on the screen."}
            )
            await ws.close()
            return

        # The browser supplies its own id, kept in local storage, so a student
        # can change their answer instead of being counted twice. It is random
        # and means nothing -- it is not a login.
        participant = str(first.get("id") or "")[:64] or secrets.token_urlsafe(9)
        await ws.send_json({"type": "joined", "id": participant})
        await live.add_voter(participant, ws)

        while True:
            message = await ws.receive_json()
            if message.get("type") == "answer":
                ok, why = await live.record(
                    participant, int(message.get("index", -1)), message.get("value")
                )
                if not ok:
                    await ws.send_json({"type": "rejected", "message": why})
    except (WebSocketDisconnect, RuntimeError, ValueError, TypeError):
        pass
    finally:
        if participant:
            await live.remove_voter(participant, ws)
