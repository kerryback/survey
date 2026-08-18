# survey

Live in-class polling at a fixed public address. The instructor drives a
full-screen display on the classroom computer; students answer from their own
phones at <https://poll.kerryback.com>.

Anonymous by design: no name field, no roster, no sign-in, nothing stored that
could identify who said what.

Driven by the [`survey` Claude Code skill](https://github.com/kerryback/skills/tree/main/plugins/survey),
which is what writes the questions and calls the API below.

## Running it in class

| key | |
| --- | --- |
| `m` | the question menu — jump to any prepared question |
| `1`–`9` | with the menu up, the question with that number |
| space or → | next question |
| ← | back |
| `o` | open or close voting |
| `h` | show or hide results |
| `r` | reveal the answer |
| `j` | put the join screen back up, and take it down again |
| Esc | close the menu |

Voting opens automatically with each question. A question with a marked `answer`
shows only a count while voting is open — closing voting or pressing `h` shows
the distribution. That withholding is deliberate: a bar chart growing in real
time tells the room what the popular answer is, and quiet students follow it.
Opinion questions have nothing to bias, so they draw live.

## Question types

| type | students see | projector shows |
| --- | --- | --- |
| `choice` | tappable options, A/B/C | a pie, each slice labelled with its % of the people who answered |
| `multi` | the same, tick any number, then Submit | a bar per option, as % of the people who answered — so they add to more than 100% |
| `wordcloud` | a text box | answers sized by how many said them |
| `scale` | a row of numbers | distribution plus the mean |
| `number` | a number box | histogram, mean, median, true answer marked |
| `rank` | a reorderable list | a heatmap: categories across, ranks down, colour by % of the room |

## The API

Everything under `/api/` takes `Authorization: Bearer $SURVEY_TOKEN`. An
unauthenticated call gets a 404 rather than a 401, so a scan of the host cannot
tell an unguessed token from a route that was never there.

| Method | Path | |
| --- | --- | --- |
| POST | `/api/session` | start a session, or adopt the running one. Returns the join link, room code and display URL |
| POST | `/api/question` | add one question and jump to it. Body is a question object |
| POST | `/api/deck` | add a prepared poll. Body `{"name": "class-4.json", "deck": {...}}` |
| POST | `/api/validate` | check a poll without loading it |
| GET | `/api/state` | everything, including every tally so far |
| GET | `/api/results.csv` | the session as CSV |
| POST | `/api/reset` | empty the session, keep the room open |
| POST | `/api/stop` | end the session |

The projector page and the QR image take a session-scoped `?key=` instead, so
the long-lived token never enters a browser URL.

A poll file:

```json
{
  "title": "MGMT 638 — Duration and convexity",
  "questions": [
    {"type": "choice", "text": "Which bond has the higher duration?",
     "options": ["The 8% coupon bond", "The 3% coupon bond", "They are equal"],
     "answer": 1},
    {"type": "wordcloud", "text": "Short answer: what does convexity buy you?"},
    {"type": "scale", "text": "How solid do you feel about duration?",
     "min": 1, "max": 5, "min_label": "Lost", "max_label": "Solid"},
    {"type": "number", "text": "Guess the 10-year Treasury yield, in percent",
     "answer": 4.3, "unit": "%"},
    {"type": "rank", "text": "Order these from least to most interest-rate risk",
     "options": ["3-month T-bills", "5-year notes", "30-year Treasuries"]}
  ]
}
```

`answer` is a 0-based index into `options`, a list of them for `"multi": true`,
or a plain number for `number`. It is optional, and including it is what makes a
question a concept check rather than an opinion poll.

## Deployment

Koyeb, org `kerrybackapps`, Docker builder, `eco-small` in `was`, redeployed on
push to `main`.

| variable | |
| --- | --- |
| `SURVEY_TOKEN` | instructor API token (a Koyeb secret) |
| `PUBLIC_URL` | `https://poll.kerryback.com` — what the QR encodes |
| `SURVEY_CODE` | optional; pins the room code instead of a fresh one per session |
| `PORT` | injected by Koyeb |

```
koyeb services logs survey/web --type runtime
koyeb services redeploy survey/web
```

## What it does not do

Nothing is persisted. One session at a time, held in memory — a redeploy or an
instance restart loses the class. Don't push on a class day, and pull the CSV
before stopping if you want it.

One answer per browser, kept by a random local id. A student can change their
mind while voting is open. It is not a login and not proof of identity: a
determined student with two browsers can vote twice. That is the right trade for
anonymity in a classroom, so don't present the numbers as an audit.

Local development:

```
pip install -r requirements.txt
PUBLIC_URL=http://127.0.0.1:8000 SURVEY_TOKEN=dev uvicorn main:app --port 8000
```
