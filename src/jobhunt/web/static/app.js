// Dashboard: while a job is running, poll its status once a second and append new log lines;
// reload once it finishes so the counts refresh.
(function () {
  const panel = document.getElementById("job");
  if (!panel || panel.dataset.status !== "running") return;
  const log = document.getElementById("job-log");
  const status = document.getElementById("job-status");
  let shown = Number(panel.dataset.lines);
  const tick = async () => {
    let job;
    try {
      const r = await fetch(`/jobs/${panel.dataset.id}`);
      if (!r.ok) return;
      job = await r.json();
    } catch (e) {
      setTimeout(tick, 2000);
      return;
    }
    for (const line of job.lines.slice(shown)) log.textContent += line + "\n";
    shown = job.lines.length;
    status.textContent = job.status;
    if (job.status === "running") setTimeout(tick, 1000);
    else location.reload();
  };
  setTimeout(tick, 1000);
})();

// Listing cards: a rating button posts the rating (plus the note) and updates the card in place.
document.querySelectorAll("form.rate").forEach((form) => {
  const buttons = form.querySelectorAll("button[name=rating]");
  const status = form.querySelector(".status");
  const submit = async (value) => {
    const body = new URLSearchParams({
      listing_id: form.dataset.id,
      rating: value,
      note: form.querySelector("input[name=note]").value,
    });
    status.textContent = "saving…";
    let r;
    try {
      r = await fetch("/ratings", { method: "POST", body });
    } catch (e) {
      status.textContent = "failed (no connection)";
      return;
    }
    if (!r.ok) {
      status.textContent = `failed (${r.status})`;
      return;
    }
    const data = await r.json();
    buttons.forEach((b) => b.classList.toggle("selected", Number(b.value) === data.rating));
    form.closest(".card").classList.add("rated");
    status.textContent = `rated ${data.rating}/5` + (data.changed ? "" : " (unchanged)");
  };
  buttons.forEach((btn) => btn.addEventListener("click", () => submit(btn.value)));
  form.addEventListener("submit", (e) => {
    // Enter in the note field re-posts the selected rating with the new note.
    e.preventDefault();
    const selected = form.querySelector("button.selected");
    if (selected) submit(selected.value);
    else status.textContent = "pick a rating first";
  });
});
