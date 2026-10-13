/* PollPulse — small helpers used on every page (no inline scripts: the
   Content-Security-Policy only allows files from /static/). */
(function () {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.split(";").map((c) => c.trim()).find((c) => c.startsWith(name + "="));
    return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
  }
  window.PollPulse = { getCookie };

  // Copy buttons: <button data-copy="#input-id">
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const input = document.querySelector(button.dataset.copy);
    if (!input) return;
    try { await navigator.clipboard.writeText(input.value); } catch (e) { input.select(); document.execCommand("copy"); }
    const label = button.querySelector("span");
    if (label) { const old = label.textContent; label.textContent = "Copied"; setTimeout(() => { label.textContent = old; }, 1600); }
  });

  // Confirmation dialogs: <form data-confirm="Are you sure?">
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-confirm]");
    if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
  });

  // Disable submit buttons after the first click (prevents accidental double submits)
  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const form = event.target;
    if (form.method && form.method.toLowerCase() === "post") {
      setTimeout(() => form.querySelectorAll("button[type=submit]").forEach((b) => { b.disabled = true; }), 0);
    }
  });

  // Success messages fade away after a few seconds
  document.querySelectorAll(".alert[data-autohide]").forEach((el) => {
    setTimeout(() => { if (window.bootstrap) bootstrap.Alert.getOrCreateInstance(el).close(); }, 6000);
  });

  // Show a block only while a radio is selected: <div data-show-when="radio-id">
  document.querySelectorAll("[data-show-when]").forEach((block) => {
    const radio = document.getElementById(block.dataset.showWhen);
    if (!radio) return;
    const update = () => block.classList.toggle("d-none", !radio.checked);
    document.querySelectorAll(`input[name="${radio.name}"]`).forEach((r) => r.addEventListener("change", update));
    update();
  });

  // Landing page demo: a tally that lives only in this browser tab
  const demo = document.getElementById("demo-poll");
  if (demo) {
    const counts = JSON.parse(demo.dataset.start);
    const buttons = demo.querySelectorAll(".demo-opt");
    const total = demo.querySelector("[data-total]");
    const render = () => {
      const sum = counts.reduce((a, b) => a + b, 0);
      buttons.forEach((button, i) => {
        const pct = sum ? Math.round((counts[i] * 1000) / sum) / 10 : 0;
        button.querySelector(".demo-pct").textContent = `${counts[i]} · ${pct}%`;
        button.querySelector(".demo-fill").style.width = `${pct}%`;
        button.setAttribute("aria-label", `${button.dataset.label}: ${counts[i]} votes, ${pct} percent. Add a vote.`);
      });
      total.textContent = `${sum} sample votes`;
    };
    buttons.forEach((button, i) => button.addEventListener("click", () => { counts[i] += 1; render(); }));
    demo.querySelector("[data-reset]").addEventListener("click", () => { counts.fill(0); render(); });
    render();
  }
})();

/* Print buttons and auto-submitting selects (kept out of inline handlers for the CSP). */
document.querySelectorAll("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));
document.querySelectorAll("select[data-autosubmit]").forEach((s) => s.addEventListener("change", () => {
  const form = s.form;
  ["qa", "qb"].forEach((name) => { const field = form.elements[name]; if (field) field.disabled = true; });
  form.submit();
}));
