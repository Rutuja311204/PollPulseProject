/*
 * Respondent form enhancements. The form works without JavaScript; this
 * file only adds convenience:
 *   - progress bar (answered required questions / all required questions)
 *   - one-question-at-a-time mode with Back / Next and a per-question check
 *   - ranking: move items up/down instead of picking numbers
 *   - "Other" text box shown only when Other is chosen
 *   - quick checks before submit (the server validates everything again)
 */
(function () {
  "use strict";
  const form = document.getElementById("respond-form");
  if (!form) return;
  const questions = Array.from(form.querySelectorAll(".respond-q"));
  const fill = document.querySelector(".progress-fill");
  const paged = form.dataset.mode === "one_per_page";

  function isAnswered(q) {
    const type = q.dataset.type;
    if (type === "ranking") return Array.from(q.querySelectorAll("select")).every((s) => s.value);
    const inputs = Array.from(q.querySelectorAll("input, select, textarea")).filter((el) => !el.classList.contains("other-text"));
    return inputs.some((el) => ((el.type === "radio" || el.type === "checkbox") ? el.checked : el.value.trim() !== ""));
  }

  function check(q) {
    const problems = [];
    const type = q.dataset.type;
    if (q.dataset.required === "1" && !isAnswered(q)) problems.push("This question is required.");
    if (type === "multiple_choice" && isAnswered(q)) {
      const n = q.querySelectorAll("input[type=checkbox]:checked").length;
      const min = Number(q.dataset.min || 0), max = Number(q.dataset.max || 0);
      if (min && n < min) problems.push(`Select at least ${min}.`);
      if (max && n > max) problems.push(`Select at most ${max}.`);
    }
    q.querySelectorAll("input[type=email], input[type=number], input[type=date], input[type=text], textarea").forEach((el) => {
      if (el.value && !el.checkValidity()) problems.push(el.validationMessage);
    });
    if (type === "ranking" && isAnswered(q)) {
      const values = Array.from(q.querySelectorAll("select")).map((s) => s.value);
      if (new Set(values).size !== values.length) problems.push("Each rank can be used only once.");
    }
    const other = q.querySelector(".other-choice");
    if (other && other.checked && !q.querySelector(".other-text").value.trim()) problems.push("Describe your “Other” answer.");
    const box = q.querySelector(".client-errors");
    box.replaceChildren(...problems.map((p) => { const d = document.createElement("div"); d.className = "field-error"; d.textContent = p; return d; }));
    q.classList.toggle("has-error", problems.length > 0);
    return problems.length === 0;
  }

  function progress() {
    const required = questions.filter((q) => q.dataset.required === "1");
    const pool = required.length ? required : questions;
    const done = pool.filter(isAnswered).length;
    if (fill) fill.style.width = `${pool.length ? (done * 100) / pool.length : 0}%`;
    const label = document.getElementById("progress-label");
    if (label) label.textContent = `${done} of ${pool.length} ${required.length ? "required " : ""}answered`;
  }

  // ---- Other text box
  questions.forEach((q) => {
    const other = q.querySelector(".other-choice");
    if (!other) return;
    const text = q.querySelector(".other-text");
    const update = () => { text.closest(".other-input").classList.toggle("d-none", !other.checked); };
    q.addEventListener("change", update);
    update();
  });

  // ---- Ranking: reorder with buttons, keep the hidden selects in sync
  questions.filter((q) => q.dataset.type === "ranking").forEach((q) => {
    const list = q.querySelector(".rank-list");
    const items = () => Array.from(list.children);
    const filled = items().every((li) => li.querySelector("select").value);
    if (!filled) {
      items().forEach((li, i) => { li.querySelector("select").value = String(i + 1); });
      q.dataset.untouched = "1";
    } else {
      items().sort((a, b) => a.querySelector("select").value - b.querySelector("select").value).forEach((li) => list.append(li));
    }
    const sync = () => items().forEach((li, i) => {
      li.querySelector("select").value = String(i + 1);
      li.querySelector(".rank-pos").textContent = String(i + 1);
      li.querySelector("[data-up]").disabled = i === 0;
      li.querySelector("[data-down]").disabled = i === items().length - 1;
    });
    list.addEventListener("click", (e) => {
      const button = e.target.closest("[data-up], [data-down]");
      if (!button) return;
      const li = button.closest("li");
      if (button.hasAttribute("data-up") && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
      if (button.hasAttribute("data-down") && li.nextElementSibling) list.insertBefore(li.nextElementSibling, li);
      delete q.dataset.untouched;
      sync();
      button.focus();
      progress();
    });
    list.classList.add("rank-enhanced");
    q.classList.add("rank-enhanced");
    sync();
  });

  // ---- One question at a time
  let current = 0;
  const nav = document.getElementById("pager-nav");
  function show(index) {
    current = Math.max(0, Math.min(index, questions.length - 1));
    questions.forEach((q, i) => q.classList.toggle("current", i === current));
    document.getElementById("step-label").textContent = `Question ${current + 1} of ${questions.length}`;
    document.getElementById("prev-btn").disabled = current === 0;
    const last = current === questions.length - 1;
    document.getElementById("next-btn").classList.toggle("d-none", last);
    document.querySelectorAll("[data-final]").forEach((el) => el.classList.toggle("d-none", !last));
    const focusable = questions[current].querySelector("input:not([type=hidden]), select, textarea");
    if (focusable && document.activeElement !== document.body) focusable.focus({ preventScroll: true });
  }
  if (paged && questions.length > 1) {
    form.classList.add("paged");
    nav.classList.remove("d-none");
    document.getElementById("prev-btn").addEventListener("click", () => show(current - 1));
    document.getElementById("next-btn").addEventListener("click", () => { if (check(questions[current])) show(current + 1); });
    const firstError = questions.findIndex((q) => q.classList.contains("has-error"));
    show(firstError >= 0 ? firstError : 0);
  }

  form.addEventListener("input", progress);
  form.addEventListener("change", (e) => {           // clear an error as soon as it is fixed
    const q = e.target.closest(".respond-q");
    if (q && q.classList.contains("has-error")) check(q);
  });
  form.addEventListener("change", progress);
  form.addEventListener("submit", (event) => {
    // An optional ranking the respondent never touched is sent as "not answered"
    questions.filter((q) => q.dataset.type === "ranking" && q.dataset.untouched === "1" && q.dataset.required !== "1")
      .forEach((q) => q.querySelectorAll("select").forEach((s) => { s.value = ""; }));
    if (event.submitter && event.submitter.value === "save_draft") return;  // drafts may be incomplete
    const firstBad = questions.findIndex((q) => !check(q));
    if (firstBad >= 0) {
      event.preventDefault();
      if (paged && questions.length > 1) show(firstBad);
      questions[firstBad].scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
  progress();
})();
