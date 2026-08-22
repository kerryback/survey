// The projector's front door. Six digits instead of the sixteen random
// characters in the display URL, because the machine at the podium is usually
// not the machine running Claude and somebody has to type this in.

const form = document.querySelector("#gate");

if (form) {
  const code = document.querySelector("#code");
  const problem = document.querySelector("#problem");
  code.focus();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.textContent = "";
    let answer;
    try {
      const response = await fetch("/api/display", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.value.trim() }),
      });
      answer = await response.json();
    } catch {
      problem.textContent = "Couldn't reach the app. Check the network.";
      return;
    }
    if (answer.url) {
      // replace rather than assign: Back from the projector should leave the
      // display, not drop the instructor onto this form again mid-class.
      location.replace(answer.url);
      return;
    }
    problem.textContent = answer.error || "That code isn't right.";
    code.select();
  });
}

// Nothing running. The podium machine can sit on this page between classes --
// it notices the next session on its own rather than waiting to be reloaded by
// somebody who is already busy starting a class.
if (!window.RUNNING) {
  setInterval(async () => {
    try {
      const response = await fetch("/healthz", { cache: "no-store" });
      if ((await response.json()).running) location.reload();
    } catch {
      /* between deploys, or the wifi. Try again on the next tick. */
    }
  }, 4000);
}
