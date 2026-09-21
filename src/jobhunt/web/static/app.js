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
