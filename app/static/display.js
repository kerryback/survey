// The projector. Shows the join details until a question is up, then the
// question and whatever the room has said so far.

const $ = (sel) => document.querySelector(sel);

// showJoin is the instructor putting the join screen back up over a live
// question, for the student who walked in late. showMenu is the list of prepared
// questions. Both are views on this projector only -- voting stays open
// underneath either one and nobody's answer is disturbed.
const state = { ws: null, current: null, showJoin: false, showMenu: false };

const CLOUD_COLOURS = [
  "#f8fafc", "#93c5fd", "#fbbf24", "#86efac", "#c4b5fd",
  "#fca5a5", "#67e8f9", "#fdba74",
];

function send(message) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(message));
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/display?key=${window.DISPLAY_KEY}`);
  state.ws = ws;
  ws.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === "state") render(message);
  };
  ws.onclose = (event) => {
    // 4403 is the server saying this session no longer exists -- it was stopped,
    // or the container restarted and took the room with it. Retrying would be a
    // projector quietly reconnecting to nothing for the rest of the day. Hand
    // back to the front door instead: it says "No session running" while there
    // is none and asks for the next display code when one starts, so a podium
    // machine nobody wants to walk over to ends up on the right screen either
    // way. Any other close is the wifi, and reconnecting is exactly right.
    if (event.code === 4403) {
      location.replace("/display");
      return;
    }
    setTimeout(connect, 1500);
  };
}

// --- rendering -------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pretty(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value * 100) / 100;
  return String(rounded);
}

function render(s) {
  const previous = state.current;
  state.current = s;

  $("#deck-title").textContent = s.title || "No poll loaded";
  if (s.student_url) {
    // Without the scheme and without the ?c= -- the room reads this off a
    // projector and types it into a phone, and the code is on the line below.
    $("#join-url").textContent = s.student_url
      .replace(/^https?:\/\//, "")
      .replace(/\/?\?c=.*$/, "");
    const qr = $("#join-qr");
    if (qr.hidden) {
      qr.src = `/api/qr.svg?key=${window.DISPLAY_KEY}`;
      qr.hidden = false;
    }
  }
  $("#join-here").textContent = s.here ? `${s.here} connected` : "";

  // What the server says is live, and what this projector is showing, are two
  // different questions once the join screen can be summoned back.
  const asking = s.loaded && s.index >= 0 && s.question;

  // `/poll <a question>` jumps straight to what it just added, which is right
  // mid-class and wrong as the first command of one: it would pull the join
  // screen down before the room had a chance to scan it, and the QR lives
  // nowhere else. So when the first question of the session goes up and nobody
  // has joined yet, put the join screen over it -- the same view `j` gives a
  // latecomer, voting open underneath. Space or `j` takes it down.
  // `here` counts everyone who has joined all session, not who is currently
  // connected, so this fires once at the start and never interrupts again.
  const wasAsking = previous && previous.loaded && previous.index >= 0 && previous.question;
  if (asking && !wasAsking && !s.here) state.showJoin = true;

  // The menu sits over whatever is up, and closes itself once there is nothing
  // to pick from.
  if (state.showMenu && !s.count) state.showMenu = false;
  drawMenu(s);

  const showingJoin = (state.showJoin || !asking) && !state.showMenu;
  $("#menu").hidden = !state.showMenu;
  $("#join").hidden = !showingJoin;
  $("#asked").hidden = showingJoin || state.showMenu;
  $("#join-live").hidden = !(state.showJoin && asking);
  if (state.showJoin && asking) {
    $("#join-live").textContent =
      `Question ${s.index + 1} is up — join now and you can still answer it`;
  }

  if (asking) {
    $("#counter").textContent = `Question ${s.index + 1} of ${s.count}`;
    $("#q-text").textContent = s.question.text;
    if (s.results) {
      $("#waiting").hidden = true;
      $("#results").hidden = false;
      drawResults(s.question, s.results, s.revealed);
    } else {
      // Results are being withheld, but the room should still see that people
      // are answering -- silence looks like a broken app.
      $("#results").hidden = true;
      $("#waiting").hidden = false;
      $("#waiting").textContent = s.responses
        ? `${s.responses} answered`
        : "Waiting for answers…";
    }
  }

  $("#status").textContent = asking ? (s.open ? "Voting open" : "Voting closed") : "";
  $("#status").className = "pill" + (s.open ? " open" : "");
  $("#tally").textContent = asking ? `${s.responses} answered · ${s.here} here` : "";

  $("#menu-button").textContent = state.showMenu ? "Close menu" : "Questions";
  $("#menu-button").disabled = !s.count;
  $("#join-screen").textContent = state.showJoin ? "Back to question" : "Join screen";
  $("#join-screen").disabled = !asking;
  $("#toggle-open").textContent = s.open ? "Close voting" : "Open voting";
  $("#toggle-open").disabled = !asking;
  $("#toggle-results").textContent = s.show_results ? "Hide results" : "Show results";
  $("#toggle-results").disabled = !asking;
  $("#reveal").disabled = !s.can_reveal;
  $("#reveal").textContent = s.revealed ? "Hide answer" : "Reveal answer";
  $("#prev").disabled = !s.loaded || s.index < 0;
  $("#next").disabled = !s.loaded || s.index >= s.count - 1;
}

// --- the question menu -----------------------------------------------------

// A prepared class is a set of questions to reach for in whatever order the
// discussion takes, not a queue to walk front to back. The server has always
// accepted a jump to any index; this is the way to ask for one.
function drawMenu(s) {
  const list = $("#menu-list");
  const signature = JSON.stringify([s.menu, s.index]);
  if (list.dataset.signature === signature) return;
  list.dataset.signature = signature;
  list.innerHTML = "";

  (s.menu || []).forEach((item) => {
    const row = el("li", "menu-item");
    if (item.index === s.index) row.classList.add("current");
    if (item.asked) row.classList.add("asked");

    // Only the first nine get a key. Past that the mouse is quicker than
    // remembering which two digits to press.
    const key = item.index < 9 ? String(item.index + 1) : "";
    row.append(el("span", "menu-key", key));
    row.append(el("span", "menu-text", item.text));
    const kind = item.multi ? "select all" : item.type;
    const note = item.responses ? `${kind} · ${item.responses} answered` : kind;
    row.append(el("span", "menu-note", note));

    row.addEventListener("click", () => pick(item.index));
    list.append(row);
  });
}

// Take the menu down on this projector before asking the server for the
// question, rather than after. Waiting for the broadcast to come back leaves the
// menu sitting on the wall for a round trip, which on classroom wifi is long
// enough to look like the click missed.
function pick(index) {
  state.showMenu = false;
  state.showJoin = false;
  if (state.current) render(state.current);
  send({ type: "goto", index });
}

function toggleMenu() {
  const s = state.current;
  if (!state.showMenu && !(s && s.count)) return;
  state.showMenu = !state.showMenu;
  if (state.showMenu) state.showJoin = false;
  if (s) render(s);
}

function drawResults(question, results, revealed) {
  const box = $("#results");
  box.innerHTML = "";
  const draw = {
    choice: drawChoice,
    wordcloud: drawCloud,
    scale: drawScale,
    number: drawNumber,
    rank: drawRank,
  }[question.type];
  if (draw) draw(box, question, results, revealed);
}

function barRow(label, fraction, value, correct) {
  const row = el("div", "bar-row" + (correct ? " correct" : ""));
  row.append(el("div", "bar-label", label));
  const track = el("div", "bar-track");
  const fill = el("div", "bar-fill");
  fill.style.width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  track.append(fill);
  row.append(track, el("div", "bar-value", value));
  return row;
}

// Select one is a pie: the options are the whole of what the room said, and a
// pie shows that as one shape rather than asking anyone to add bars up. Select
// all is not -- there the pieces do not make a whole, so it stays bars.
function drawChoice(box, question, results, revealed) {
  if (!results.multi) {
    drawPie(box, question, results, revealed);
    return;
  }
  const bars = el("div", "bars");
  const correct = new Set(revealed ? results.answer || [] : []);
  results.options.forEach((option, index) => {
    const pct = Math.round(option.share * 100);
    bars.append(
      barRow(option.text, option.share, `${option.count} · ${pct}%`, correct.has(index))
    );
  });
  box.append(bars);
  // Percentages are of respondents, so on a select-all they add to more than
  // 100. Say so once on the screen rather than letting the room do the
  // arithmetic and conclude the chart is broken.
  if (results.multi && results.responses) {
    box.append(
      summary([["Select all — % of the", `${results.responses} who answered`]])
    );
  }
}


// --- the pie ---------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

const PIE_COLOURS = [
  "#93c5fd", "#fbbf24", "#86efac", "#c4b5fd",
  "#fca5a5", "#67e8f9", "#fdba74", "#f8fafc",
];

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function slicePath(cx, cy, r, startDeg, endDeg) {
  const point = (deg) => {
    const a = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const [x1, y1] = point(startDeg);
  const [x2, y2] = point(endDeg);
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
}

function drawPie(box, question, results, revealed) {
  if (!results.responses) {
    box.append(el("p", "waiting", "Waiting for answers…"));
    return;
  }

  const wrap = el("div", "pie-wrap");
  const svg = svgEl("svg", { viewBox: "0 0 100 100", class: "pie" });
  const correct = new Set(revealed ? results.answer || [] : []);
  // Start at twelve o'clock and go clockwise, which is the direction everyone
  // reads a pie in.
  let angle = -90;

  results.options.forEach((option, index) => {
    if (!option.count) return;
    const colour = PIE_COLOURS[index % PIE_COLOURS.length];
    const sweep = option.share * 360;
    // A lone option taking every vote is a full circle, and an arc from a point
    // back to itself draws nothing at all.
    const shape =
      sweep >= 359.99
        ? svgEl("circle", { cx: 50, cy: 50, r: 46, fill: colour })
        : svgEl("path", { d: slicePath(50, 50, 46, angle, angle + sweep), fill: colour });
    if (correct.has(index)) shape.setAttribute("class", "correct");
    svg.append(shape);
    angle += sweep;
  });

  const legend = el("div", "pie-legend");
  results.options.forEach((option, index) => {
    const row = el("div", "pie-key" + (correct.has(index) ? " correct" : ""));
    const swatch = el("span", "pie-swatch");
    swatch.style.background = PIE_COLOURS[index % PIE_COLOURS.length];
    if (!option.count) swatch.classList.add("empty");
    row.append(swatch);
    row.append(el("span", "pie-name", option.text));
    row.append(el("span", "pie-pct", `${Math.round(option.share * 100)}%`));
    legend.append(row);
  });

  wrap.append(svg, legend);
  box.append(wrap);
}

function drawCloud(box, question, results) {
  if (!results.words.length) {
    box.append(el("p", "waiting", "Waiting for answers…"));
    return;
  }
  const cloud = el("div", "cloud");
  results.words.forEach((entry, index) => {
    const word = el("span", null, entry.word);
    // Scale by the square root of the weight: linear scaling makes the top
    // word enormous and everything else unreadable.
    let size = 1.1 + Math.sqrt(entry.weight) * 3.6;
    // A two- or three-word answer is already several times wider than a single
    // word, so at the same point size it would swamp the cloud on width alone.
    const words = entry.word.split(" ").length;
    if (words > 1) size /= words === 2 ? 1.4 : 1.75;
    word.style.fontSize = `${size}rem`;
    word.style.color = CLOUD_COLOURS[index % CLOUD_COLOURS.length];
    word.title = `${entry.count}`;
    cloud.append(word);
  });
  box.append(cloud);
}

function columns(entries) {
  const wrap = el("div", "columns");
  entries.forEach((entry) => {
    const column = el("div", "column");
    column.append(el("div", "count", entry.count ? String(entry.count) : ""));
    const stalk = el("div", "stalk");
    stalk.style.height = `${Math.max(0, Math.min(1, entry.weight)) * 100}%`;
    if (entry.highlight) stalk.style.background = "var(--good)";
    column.append(stalk, el("div", "tick", entry.tick));
    wrap.append(column);
  });
  return wrap;
}

function summary(items) {
  const wrap = el("div", "summary");
  items.forEach(([label, value, className]) => {
    const item = el("div", className || "");
    item.append(document.createTextNode(label + " "));
    item.append(el("strong", null, value));
    wrap.append(item);
  });
  return wrap;
}

function drawScale(box, question, results) {
  box.append(
    columns(results.points.map((p) => ({ count: p.count, weight: p.weight, tick: p.value })))
  );
  const ends = el("div", "ends");
  ends.append(el("span", null, results.min_label), el("span", null, results.max_label));
  box.append(ends);
  if (results.mean !== null) box.append(summary([["mean", pretty(results.mean)]]));
}

function drawNumber(box, question, results) {
  if (!results.bins.length) {
    box.append(el("p", "waiting", "Waiting for answers…"));
    return;
  }
  const actual = results.answer;
  box.append(
    columns(
      results.bins.map((bin) => ({
        count: bin.count,
        weight: bin.weight,
        tick: pretty(bin.start),
        highlight: actual !== null && actual !== undefined && actual >= bin.start && actual <= bin.end,
      }))
    )
  );
  const items = [
    ["mean", pretty(results.mean) + results.unit],
    ["median", pretty(results.median) + results.unit],
  ];
  if (actual !== null && actual !== undefined) {
    items.push(["actual", pretty(actual) + results.unit, "actual"]);
  }
  box.append(summary(items));
}

function drawRank(box, question, results) {
  if (!results.complete) {
    box.append(el("p", "waiting", "Waiting for answers…"));
    return;
  }

  // Categories across, ranks down, colour by how much of the room put that
  // category in that position. The average alone would say "third" for an
  // option everybody ranked third and for one split between first and fifth,
  // which are opposite findings about the room.
  const options = results.options || [];
  const grid = el("div", "heat");
  grid.style.gridTemplateColumns = `auto repeat(${options.length}, minmax(0, 1fr))`;

  grid.append(el("div", "heat-corner", ""));
  options.forEach((text) => grid.append(el("div", "heat-head", text)));

  results.heat.forEach((row) => {
    grid.append(el("div", "heat-rank", ordinal(row.rank)));
    row.cells.forEach((cell) => {
      const box = el("div", "heat-cell", cell.share ? `${Math.round(cell.share * 100)}%` : "");
      // Floor the alpha so a cell with a couple of votes still reads as warm
      // rather than as empty, and keep the text light on the darkest cells.
      box.style.background = `rgba(59, 130, 246, ${cell.share ? 0.18 + cell.share * 0.82 : 0})`;
      if (cell.share >= 0.55) box.classList.add("strong");
      grid.append(box);
    });
  });

  box.append(grid);
  box.append(summary([["of the", `${results.complete} who ranked`]]));
}

function ordinal(n) {
  const suffix = ["th", "st", "nd", "rd"][(n % 100 - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th";
  return `${n}${suffix}`;
}

// --- controls --------------------------------------------------------------

function step(delta) {
  const s = state.current;
  if (!s || !s.loaded) return;
  // Moving on means the latecomer is in and the room is back to work.
  state.showJoin = false;
  state.showMenu = false;
  send({ type: "goto", index: Math.max(-1, Math.min(s.index + delta, s.count - 1)) });
}

function toggleJoin() {
  state.showJoin = !state.showJoin;
  if (state.showJoin) state.showMenu = false;
  if (state.current) render(state.current);
}

$("#join-screen").addEventListener("click", toggleJoin);
$("#menu-button").addEventListener("click", toggleMenu);
$("#prev").addEventListener("click", () => step(-1));
$("#next").addEventListener("click", () => step(1));
$("#toggle-open").addEventListener("click", () =>
  send({ type: "open", on: !(state.current && state.current.open) })
);
$("#toggle-results").addEventListener("click", () =>
  send({ type: "results", on: !(state.current && state.current.show_results) })
);
$("#reveal").addEventListener("click", () =>
  send({ type: "reveal", on: !(state.current && state.current.revealed) })
);

document.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  // With the menu up, a digit is a question number. Everywhere else it means
  // nothing, so this doesn't cost any other key its job.
  if (state.showMenu && /^[1-9]$/.test(event.key)) {
    const wanted = Number(event.key) - 1;
    const item = ((state.current && state.current.menu) || [])[wanted];
    if (item) {
      event.preventDefault();
      pick(item.index);
      showBar();
    }
    return;
  }

  const keys = {
    ArrowRight: () => step(1),
    " ": () => step(1),
    ArrowLeft: () => step(-1),
    o: () => $("#toggle-open").click(),
    h: () => $("#toggle-results").click(),
    r: () => $("#reveal").click(),
    j: toggleJoin,
    m: toggleMenu,
    Escape: () => {
      if (state.showMenu) toggleMenu();
    },
  };
  const action = keys[event.key];
  if (action) {
    event.preventDefault();
    action();
    showBar();
  }
});

// The control bar is on the projector too, so it stays out of the way until
// the instructor reaches for it.
let barTimer = null;
function showBar() {
  $("#bar").classList.add("shown");
  clearTimeout(barTimer);
  barTimer = setTimeout(() => $("#bar").classList.remove("shown"), 3500);
}
document.addEventListener("mousemove", showBar);

connect();
showBar();
