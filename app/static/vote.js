// The student's device. One question at a time, whatever the instructor has up.
// No name is ever collected; the id below is random and local to this browser,
// and exists only so that changing your answer replaces it instead of counting
// twice.

const $ = (sel) => document.querySelector(sel);

const LETTERS = "ABCDEFGHIJ";

function participantId() {
  let id = null;
  try {
    id = localStorage.getItem("poll-id");
    if (!id) {
      id = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
      localStorage.setItem("poll-id", id);
    }
  } catch {
    id = Math.random().toString(36).slice(2); // private browsing: fine, just per-tab
  }
  return id;
}

const state = {
  ws: null,
  id: participantId(),
  code: "",
  joined: false,
  current: null,
  renderedFor: null,
  draft: null,
};

function status(text, tone = "") {
  $("#status").textContent = text;
  $("#status").className = "status" + (tone ? " " + tone : "");
}

function send(message) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(message));
  }
}

function answer(value) {
  const s = state.current;
  if (!s || !s.question) return;
  send({ type: "answer", index: s.index, value });
}

// --- connection ------------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/vote`);
  state.ws = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "join", code: state.code, id: state.id }));
  ws.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === "joined") {
      state.joined = true;
      $("#join-form").hidden = true;
      $("#live").hidden = false;
    } else if (message.type === "error") {
      // The server is about to hang up. Don't treat it as a dropped connection
      // worth retrying -- a wrong code retried forever is forty phones knocking.
      state.joined = false;
      status(message.message, "bad");
    } else if (message.type === "rejected") {
      status(message.message, "bad");
    } else if (message.type === "state") {
      render(message);
    }
  };
  ws.onclose = (event) => {
    if (event.code === 4403) {
      state.joined = false;
      status("The poll has ended. Thanks!", "bad");
      return;
    }
    if (state.joined) setTimeout(connect, 1500);
  };
}

$("#join-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.code = $("#code").value.trim();
  if (!state.code) return;
  status("Joining…");
  connect();
});

// The QR on the projector carries the code, so a student who scanned it has
// nothing to type. One who typed the address in still does.
const scanned = new URLSearchParams(location.search).get("c");
if (scanned && $("#code")) $("#code").value = scanned.trim();

// --- the question ----------------------------------------------------------

function render(s) {
  state.current = s;
  const box = $("#input");

  if (!s.question) {
    $("#counter").textContent = "";
    $("#q-text").textContent = "Waiting for the first question…";
    box.innerHTML = "";
    state.renderedFor = null;
    status("");
    return;
  }

  $("#counter").textContent = `Question ${s.index + 1}`;
  $("#q-text").textContent = s.question.text;

  // Rebuild only when something structural changes, so typing isn't wiped out
  // by an unrelated update from the room.
  const signature = [s.index, s.open, s.revealed, s.answered].join("|");
  if (signature !== state.renderedFor) {
    state.renderedFor = signature;
    state.draft = null;
    box.innerHTML = "";
    build(box, s);
  }

  if (!s.open) {
    status(s.answered ? "Voting closed. Your answer was counted." : "Voting is closed.");
  } else if (s.answered) {
    status("Answer recorded. You can change it while voting is open.", "done");
  } else {
    status("");
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function build(box, s) {
  const builders = {
    choice: buildChoice,
    wordcloud: buildText,
    scale: buildScale,
    number: buildNumber,
    rank: buildRank,
  };
  const builder = builders[s.question.type];
  if (builder) builder(box, s);
}

function buildChoice(box, s) {
  const question = s.question;
  const multi = !!question.multi;
  const correct = new Set(s.revealed && s.answer ? s.answer : []);
  const mine = new Set(
    Array.isArray(s.mine) ? s.mine : s.mine === null || s.mine === undefined ? [] : [s.mine]
  );
  state.draft = new Set(mine);

  const list = el("div", "choices");
  question.options.forEach((text, index) => {
    const button = el("button", "choice");
    button.type = "button";
    button.append(el("span", "letter", LETTERS[index] || String(index + 1)));
    button.append(el("span", "label", text));
    if (mine.has(index)) button.classList.add("picked");
    if (correct.has(index)) button.classList.add("correct");
    button.disabled = !s.open;

    button.addEventListener("click", () => {
      if (multi) {
        if (state.draft.has(index)) {
          state.draft.delete(index);
          button.classList.remove("picked");
        } else {
          state.draft.add(index);
          button.classList.add("picked");
        }
      } else {
        list.querySelectorAll(".choice").forEach((b) => b.classList.remove("picked"));
        button.classList.add("picked");
        answer(index);
      }
    });
    list.append(button);
  });
  box.append(list);

  if (multi) {
    const submit = el("button", "primary", "Submit");
    submit.type = "button";
    submit.disabled = !s.open;
    submit.addEventListener("click", () => answer([...state.draft]));
    box.append(submit);
  }
}

function buildText(box, s) {
  const input = el("input");
  input.type = "text";
  input.maxLength = 200;
  input.placeholder = "Type your answer";
  input.value = typeof s.mine === "string" ? s.mine : "";
  input.disabled = !s.open;

  const submit = el("button", "primary", "Submit");
  submit.type = "button";
  submit.disabled = !s.open;

  const go = () => {
    if (input.value.trim()) answer(input.value.trim());
  };
  submit.addEventListener("click", go);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") go();
  });

  const wrap = el("div", "stack");
  wrap.style.marginTop = "1rem";
  wrap.append(input, submit);
  box.append(wrap);
}

function buildScale(box, s) {
  const question = s.question;
  const row = el("div", "scale-row");
  for (let value = question.min; value <= question.max; value += 1) {
    const button = el("button", null, String(value));
    button.type = "button";
    button.disabled = !s.open;
    if (s.mine === value) button.classList.add("picked");
    button.addEventListener("click", () => {
      row.querySelectorAll("button").forEach((b) => b.classList.remove("picked"));
      button.classList.add("picked");
      answer(value);
    });
    row.append(button);
  }
  box.append(row);

  const ends = el("div", "scale-ends");
  ends.append(
    el("span", null, question.min_label || ""),
    el("span", null, question.max_label || "")
  );
  box.append(ends);
}

function buildNumber(box, s) {
  const input = el("input");
  input.type = "number";
  input.step = "any";
  input.placeholder = s.question.unit ? `Number in ${s.question.unit}` : "Your estimate";
  if (typeof s.mine === "number") input.value = String(s.mine);
  input.disabled = !s.open;

  const submit = el("button", "primary", "Submit");
  submit.type = "button";
  submit.disabled = !s.open;

  const go = () => {
    const value = parseFloat(input.value);
    if (Number.isFinite(value)) answer(value);
    else status("Type a number first.", "bad");
  };
  submit.addEventListener("click", go);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") go();
  });

  const wrap = el("div", "stack");
  wrap.style.marginTop = "1rem";
  wrap.append(input, submit);
  box.append(wrap);

  if (s.revealed && s.answer !== null && s.answer !== undefined) {
    box.append(el("p", "status done", `Answer: ${s.answer}${s.question.unit || ""}`));
  }
}

function buildRank(box, s) {
  const options = s.question.options;
  state.draft = Array.isArray(s.mine) && s.mine.length === options.length
    ? [...s.mine]
    : options.map((_, i) => i);

  const list = el("ul", "rank-list");

  const paint = () => {
    list.innerHTML = "";
    state.draft.forEach((optionIndex, position) => {
      const row = el("li");
      row.append(el("span", "pos", String(position + 1)));
      row.append(el("span", "label", options[optionIndex]));

      const up = el("button", null, "▲");
      up.type = "button";
      up.disabled = position === 0 || !s.open;
      up.addEventListener("click", () => {
        const order = state.draft;
        [order[position - 1], order[position]] = [order[position], order[position - 1]];
        paint();
      });

      const down = el("button", null, "▼");
      down.type = "button";
      down.disabled = position === state.draft.length - 1 || !s.open;
      down.addEventListener("click", () => {
        const order = state.draft;
        [order[position], order[position + 1]] = [order[position + 1], order[position]];
        paint();
      });

      row.append(up, down);
      list.append(row);
    });
  };
  paint();
  box.append(list);

  const submit = el("button", "primary", "Submit ranking");
  submit.type = "button";
  submit.disabled = !s.open;
  submit.style.marginTop = "1rem";
  submit.addEventListener("click", () => answer([...state.draft]));
  box.append(submit);
}
