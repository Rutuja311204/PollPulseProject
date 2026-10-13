/*
 * PollPulse survey builder.
 *
 * State lives in `questions` (an array of plain objects with the same shape
 * the server stores). Typing updates the state; structural changes (add,
 * move, delete, change type) re-render the list. "Save draft" sends the whole
 * list to PUT /api/surveys/<id>/questions/, where the server validates and
 * normalises it (the browser is never trusted). The response also carries
 * the quality-check issues shown in the side panel.
 *
 * All user text is inserted with textContent / input.value — never innerHTML —
 * so a question titled "<script>" is displayed as text.
 */
(function () {
  "use strict";
  const dataEl = document.getElementById("builder-data");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const TYPES = Object.fromEntries(data.types.map((t) => [t.key, t]));
  const editable = data.canEdit && !data.locked;

  let questions = JSON.parse(JSON.stringify(data.questions));
  let issues = data.issues || [];
  let selected = questions.length ? 0 : -1;
  let dirty = false;
  let saving = false;

  const listEl = document.getElementById("question-list");
  const previewEl = document.getElementById("preview");
  const stateEl = document.getElementById("save-state");

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === false || value === null || value === undefined) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else if (key === "checked") el.checked = !!value;
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const icon = (name) => h("i", { class: `bi ${name}`, "aria-hidden": "true" });
  const clone = (obj) => JSON.parse(JSON.stringify(obj));

  function setState(kind, text) {
    stateEl.className = `save-state ${kind}`;
    stateEl.replaceChildren(icon(kind === "dirty" ? "bi-pencil" : kind === "saved" ? "bi-cloud-check" : "bi-arrow-repeat"), text);
  }
  function markDirty() {
    dirty = true;
    setState("dirty", "Unsaved changes");
  }

  function newQuestion(type) {
    const t = TYPES[type];
    return {
      type, title: "", description: "", required: false,
      config: clone(t.defaultConfig),
      options: t.hasOptions ? [{ text: "Option 1" }, { text: "Option 2" }] : [],
    };
  }

  function issuesFor(q) {
    return issues.filter((i) => q.qid && i.qid === q.qid);
  }

  // ---------------------------------------------------------------- config editors
  function numberField(q, key, label, attrs = {}) {
    return h("div", { class: "col-6" },
      h("label", { class: "form-label", text: label }),
      h("input", {
        class: "form-control", type: "number", value: q.config[key] ?? "", disabled: !editable, ...attrs,
        oninput: (e) => { q.config[key] = e.target.value === "" ? null : Number(e.target.value); markDirty(); renderPreview(); },
      }));
  }
  function selectField(q, key, label, choices) {
    return h("div", { class: "col-6" },
      h("label", { class: "form-label", text: label }),
      h("select", {
        class: "form-select", disabled: !editable,
        onchange: (e) => { q.config[key] = Number(e.target.value); markDirty(); renderPreview(); },
      }, choices.map((c) => h("option", { value: c, selected: Number(q.config[key]) === c ? "selected" : false, text: String(c) }))));
  }
  function textField(q, key, label, attrs = {}) {
    return h("div", { class: "col-6" },
      h("label", { class: "form-label", text: label }),
      h("input", {
        class: "form-control", value: q.config[key] || "", disabled: !editable, ...attrs,
        oninput: (e) => { q.config[key] = e.target.value; markDirty(); renderPreview(); },
      }));
  }
  function checkField(q, key, label) {
    const id = `cfg-${key}-${Math.random().toString(36).slice(2, 7)}`;
    return h("div", { class: "form-check" },
      h("input", {
        class: "form-check-input", type: "checkbox", id, checked: !!q.config[key], disabled: !editable,
        onchange: (e) => { q.config[key] = e.target.checked; markDirty(); renderAll(); },
      }),
      h("label", { class: "form-check-label", for: id, text: label }));
  }

  function configEditor(q) {
    const row = h("div", { class: "row g-2" });
    switch (q.type) {
      case "multiple_choice":
        row.append(numberField(q, "min_select", "Minimum selections", { min: 0 }),
                   numberField(q, "max_select", "Maximum selections (blank = no limit)", { min: 1 }));
        break;
      case "short_text": case "long_text":
        row.append(numberField(q, "min_length", "Minimum characters", { min: 0 }),
                   numberField(q, "max_length", "Maximum characters", { min: 1 }));
        break;
      case "number":
        row.append(numberField(q, "min_value", "Minimum value"), numberField(q, "max_value", "Maximum value"),
                   h("div", { class: "col-12" }, checkField(q, "integer_only", "Whole numbers only")));
        break;
      case "rating":
        row.append(selectField(q, "max", "Number of stars", [3, 4, 5, 6, 7, 8, 9, 10]));
        break;
      case "linear_scale":
        row.append(selectField(q, "min", "Scale starts at", [0, 1]), selectField(q, "max", "Scale ends at", [2, 3, 4, 5, 6, 7, 8, 9, 10]),
                   textField(q, "min_label", "Label for the lowest value", { maxlength: 40 }),
                   textField(q, "max_label", "Label for the highest value", { maxlength: 40 }));
        break;
      case "date":
        row.append(textField(q, "min_date", "Earliest date", { type: "date" }), textField(q, "max_date", "Latest date", { type: "date" }));
        break;
      default:
        return null;
    }
    return h("div", {}, h("div", { class: "form-label mb-1", text: "Answer rules" }), row);
  }

  function optionsEditor(q) {
    const t = TYPES[q.type];
    if (!t.hasOptions) return null;
    const markClass = q.type === "multiple_choice" ? "opt-mark square" : q.type === "ranking" ? "opt-mark rank" : "opt-mark";
    const rows = q.options.map((opt, i) => h("div", { class: "opt-row" },
      h("span", { class: markClass, text: q.type === "ranking" ? String(i + 1) : "" }),
      h("input", {
        class: "form-control form-control-sm", value: opt.text, maxlength: 150, disabled: !editable,
        "aria-label": `Option ${i + 1}`, placeholder: `Option ${i + 1}`,
        oninput: (e) => { opt.text = e.target.value; markDirty(); renderPreview(); },
        onkeydown: (e) => {
          if (e.key === "Enter" && editable) { e.preventDefault(); addOption(q, i + 1); }
        },
      }),
      editable ? h("button", { type: "button", class: "icon-btn", title: "Move up", "aria-label": "Move option up", disabled: i === 0, onclick: () => moveItem(q.options, i, -1) }, icon("bi-arrow-up")) : null,
      editable ? h("button", { type: "button", class: "icon-btn", title: "Move down", "aria-label": "Move option down", disabled: i === q.options.length - 1, onclick: () => moveItem(q.options, i, 1) }, icon("bi-arrow-down")) : null,
      editable ? h("button", { type: "button", class: "icon-btn danger", title: "Remove option", "aria-label": "Remove option", onclick: () => { q.options.splice(i, 1); markDirty(); renderAll(); } }, icon("bi-x-lg")) : null,
    ));
    return h("div", {},
      h("div", { class: "form-label mb-1", text: q.type === "ranking" ? "Items to rank" : "Answer options" }),
      rows,
      editable ? h("button", { type: "button", class: "btn btn-quiet btn-sm", disabled: q.options.length >= t.maxOptions, onclick: () => addOption(q, q.options.length) }, icon("bi-plus-circle"), "Add option") : null,
      h("span", { class: "small-print ms-2", text: `${t.minOptions}–${t.maxOptions} options` }),
      t.supportsOther ? h("div", { class: "mt-2" }, checkField(q, "allow_other", "Add an “Other” choice with a text box")) : null,
    );
  }

  function addOption(q, index) {
    if (q.options.length >= TYPES[q.type].maxOptions) return;
    q.options.splice(index, 0, { text: "" });
    markDirty();
    renderAll();
    const inputs = listEl.querySelectorAll(".q-card.selected .opt-row input");
    if (inputs[index]) inputs[index].focus();
  }

  function moveItem(array, index, delta) {
    const target = index + delta;
    if (target < 0 || target >= array.length) return;
    [array[index], array[target]] = [array[target], array[index]];
    markDirty();
    renderAll();
  }

  // ---------------------------------------------------------------- question cards
  function changeType(q, type) {
    const old = TYPES[q.type], next = TYPES[type];
    q.type = type;
    q.config = clone(next.defaultConfig);
    if (!next.hasOptions) q.options = [];
    else if (!old.hasOptions || !q.options.length) q.options = [{ text: "Option 1" }, { text: "Option 2" }];
    q.options = q.options.slice(0, next.maxOptions);
    markDirty();
    renderAll();
  }

  function card(q, index) {
    const t = TYPES[q.type];
    const qIssues = issuesFor(q);
    const isSelected = index === selected;
    const titleEl = h("div", { class: `q-title ${q.title ? "" : "empty-title"}`, text: q.title || "Untitled question" });
    const head = h("div", {
      class: "q-card-head", role: "button", tabindex: "0", "aria-expanded": String(isSelected),
      onclick: (e) => { if (!e.target.closest("button")) select(index); },
      onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && !e.target.closest("button")) { e.preventDefault(); select(index); } },
    },
      h("span", { class: "q-num", text: `Q${index + 1}` }),
      h("div", { class: "q-summary" }, titleEl,
        h("div", { class: "q-type" }, icon(t.icon), t.label, q.required ? h("span", { class: "req", text: "· required" }) : null,
          qIssues.some((i) => i.level === "error") ? h("span", { class: "text-danger ms-1" }, icon("bi-exclamation-circle-fill")) : null)),
      editable ? h("div", { class: "q-tools" },
        h("button", { type: "button", class: "icon-btn", title: "Move up", "aria-label": `Move question ${index + 1} up`, disabled: index === 0, onclick: () => { moveItem(questions, index, -1); selected = index - 1; renderAll(); } }, icon("bi-arrow-up")),
        h("button", { type: "button", class: "icon-btn", title: "Move down", "aria-label": `Move question ${index + 1} down`, disabled: index === questions.length - 1, onclick: () => { moveItem(questions, index, 1); selected = index + 1; renderAll(); } }, icon("bi-arrow-down")),
        h("button", { type: "button", class: "icon-btn", title: "Duplicate", "aria-label": `Duplicate question ${index + 1}`, onclick: () => duplicate(index) }, icon("bi-copy")),
        h("button", { type: "button", class: "icon-btn danger", title: "Delete", "aria-label": `Delete question ${index + 1}`, onclick: () => remove(index) }, icon("bi-trash3")),
      ) : null,
    );

    const body = h("div", { class: "q-card-body" });
    if (isSelected) {
      const typeSelect = h("select", { class: "form-select", id: `type-${index}`, disabled: !editable, onchange: (e) => changeType(q, e.target.value) },
        data.types.map((tt) => h("option", { value: tt.key, selected: tt.key === q.type ? "selected" : false, text: tt.label })));
      const reqId = `req-${index}`;
      body.append(h("div", { class: "row-gap" },
        h("div", { class: "row g-2" },
          h("div", { class: "col-md-7" }, h("label", { class: "form-label", for: `title-${index}`, text: "Question" }),
            h("input", { class: "form-control", id: `title-${index}`, value: q.title, maxlength: 300, disabled: !editable, placeholder: "Type the question respondents will see",
              oninput: (e) => { q.title = e.target.value; titleEl.textContent = q.title || "Untitled question"; titleEl.classList.toggle("empty-title", !q.title); markDirty(); renderPreview(); } })),
          h("div", { class: "col-md-5" }, h("label", { class: "form-label", for: `type-${index}`, text: "Type" }), typeSelect)),
        h("div", {}, h("label", { class: "form-label", for: `desc-${index}`, text: "Help text (optional)" }),
          h("textarea", { class: "form-control", id: `desc-${index}`, rows: 2, maxlength: 500, disabled: !editable, placeholder: "Extra context shown under the question",
            oninput: (e) => { q.description = e.target.value; markDirty(); renderPreview(); } }, q.description || "")),
        h("div", { class: "form-check form-switch" },
          h("input", { class: "form-check-input", type: "checkbox", role: "switch", id: reqId, checked: q.required, disabled: !editable,
            onchange: (e) => { q.required = e.target.checked; markDirty(); renderAll(); } }),
          h("label", { class: "form-check-label", for: reqId, text: "Required — respondents must answer" })),
        optionsEditor(q),
        configEditor(q),
        qIssues.length ? h("div", {}, qIssues.map((i) => h("div", { class: `issue issue-${i.level} py-1` },
          h("span", { class: "issue-icon" }, icon(i.level === "error" ? "bi-x-lg" : i.level === "warning" ? "bi-exclamation-lg" : "bi-lightbulb")),
          h("div", {}, i.message, h("span", { class: "suggestion", text: i.suggestion }))))) : null,
      ));
    }
    return h("div", { class: `q-card ${isSelected ? "selected" : ""} ${qIssues.some((i) => i.level === "error") ? "has-error" : ""}`, "data-index": index }, head, body);
  }

  function select(index) {
    selected = index;
    renderAll();
  }

  function duplicate(index) {
    const copy = clone(questions[index]);
    delete copy.qid;
    copy.options = copy.options.map((o) => ({ text: o.text }));
    copy.title = copy.title ? `${copy.title} (copy)`.slice(0, 300) : "";
    questions.splice(index + 1, 0, copy);
    selected = index + 1;
    markDirty();
    renderAll();
  }

  function remove(index) {
    const q = questions[index];
    if ((q.title || q.options.some((o) => o.text)) && !window.confirm(`Delete question ${index + 1}?`)) return;
    questions.splice(index, 1);
    selected = Math.min(selected, questions.length - 1);
    markDirty();
    renderAll();
  }

  function addQuestion(type, preset) {
    const q = preset ? clone(preset) : newQuestion(type);
    if (preset) { delete q.qid; q.options = (q.options || []).map((o) => ({ text: o.text })); delete q.rationale; }
    questions.push(q);
    selected = questions.length - 1;
    markDirty();
    renderAll();
    const input = document.getElementById(`title-${selected}`);
    if (input && !preset) input.focus();
  }

  // ---------------------------------------------------------------- preview
  function renderPreview() {
    const q = questions[selected];
    previewEl.replaceChildren();
    if (!q) { previewEl.append(h("p", { class: "muted mb-0", text: "Select a question to preview it." })); return; }
    const t = TYPES[q.type];
    const name = `pv-${selected}`;
    const area = h("div", { class: "answer-area" });
    const opts = q.options.filter((o) => o.text.trim());
    const choice = (type, label) => h("label", { class: "choice" }, h("input", { type, name }), h("span", { class: "pick" }), h("span", { text: label }));
    switch (q.type) {
      case "single_choice": case "multiple_choice":
        opts.forEach((o) => area.append(choice(q.type === "single_choice" ? "radio" : "checkbox", o.text)));
        if (q.config.allow_other) area.append(choice(q.type === "single_choice" ? "radio" : "checkbox", "Other"));
        break;
      case "dropdown":
        area.append(h("select", { class: "form-select" }, h("option", { text: "Choose…" }), opts.map((o) => h("option", { text: o.text }))));
        break;
      case "yes_no":
        area.append(h("div", { class: "yesno" }, choice("radio", "Yes"), choice("radio", "No")));
        break;
      case "short_text": area.append(h("input", { class: "form-control", placeholder: "Your answer" })); break;
      case "long_text": area.append(h("textarea", { class: "form-control", rows: 3, placeholder: "Your answer" })); break;
      case "email": area.append(h("input", { class: "form-control", type: "email", placeholder: "name@example.com" })); break;
      case "number": area.append(h("input", { class: "form-control", type: "number", min: q.config.min_value ?? false, max: q.config.max_value ?? false })); break;
      case "date": area.append(h("input", { class: "form-control", type: "date", min: q.config.min_date || false, max: q.config.max_date || false })); break;
      case "rating": {
        const stars = h("div", { class: "stars" });
        for (let v = Number(q.config.max) || 5; v >= 1; v--) {
          const id = `pv-star-${v}`;
          stars.append(h("input", { type: "radio", name, id, value: v }), h("label", { for: id, title: `${v}` }, icon("bi-star-fill")));
        }
        area.append(stars);
        break;
      }
      case "linear_scale": {
        const scale = h("div", { class: "scale" });
        for (let v = Number(q.config.min ?? 1); v <= Number(q.config.max ?? 5); v++) {
          scale.append(h("label", { class: "scale-opt" }, h("input", { type: "radio", name, value: v }), h("span", { text: String(v) })));
        }
        area.append(scale, h("div", { class: "scale-labels" }, h("span", { text: q.config.min_label || "" }), h("span", { text: q.config.max_label || "" })));
        break;
      }
      case "ranking":
        area.append(h("ol", { class: "rank-list" }, opts.map((o, i) => h("li", { class: "rank-item" }, h("span", { class: "rank-pos", text: String(i + 1) }), h("span", { class: "rank-label", text: o.text })))));
        break;
      default: break;
    }
    previewEl.append(h("fieldset", { class: "respond-q" },
      h("legend", {}, h("span", { class: "q-index", text: String(selected + 1) }), h("span", {}, q.title || "Untitled question", q.required ? h("span", { class: "req", "aria-hidden": "true", text: "*" }) : null)),
      q.description ? h("p", { class: "q-desc pre-line", text: q.description }) : null,
      area));
    document.getElementById("preview-label").textContent = `Q${selected + 1} · ${t.label}`;
  }

  // ---------------------------------------------------------------- issues
  function renderIssues() {
    const box = document.getElementById("issues");
    box.replaceChildren();
    const counts = { error: 0, warning: 0, tip: 0 };
    issues.forEach((i) => { counts[i.level] += 1; });
    document.getElementById("issue-count").textContent = `${counts.error} blocking, ${counts.warning} warnings, ${counts.tip} tips`;
    if (!issues.length) { box.append(h("p", { class: "muted mb-0" }, icon("bi-check2-circle text-success me-1"), "No issues found.")); return; }
    issues.forEach((i) => box.append(h("div", { class: `issue issue-${i.level}` },
      h("span", { class: "issue-icon" }, icon(i.level === "error" ? "bi-x-lg" : i.level === "warning" ? "bi-exclamation-lg" : "bi-lightbulb")),
      h("div", {}, i.number ? h("button", { type: "button", class: "btn btn-link p-0 q-ref align-baseline", text: `Q${i.number}`, onclick: () => select(i.number - 1) }) : null, " ", i.message,
        h("span", { class: "suggestion", text: i.suggestion })))));
  }

  function renderAll() {
    listEl.replaceChildren(...questions.map(card));
    document.getElementById("builder-empty").classList.toggle("d-none", questions.length > 0);
    renderPreview();
    renderIssues();
  }

  // ---------------------------------------------------------------- server calls
  async function api(url, method, body) {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRFToken": window.PollPulse.getCookie("csrftoken") },
      credentials: "same-origin",
      body: body ? JSON.stringify(body) : undefined,
    });
    let payload = {};
    try { payload = await response.json(); } catch (e) { /* empty body */ }
    return { ok: response.ok, status: response.status, payload };
  }

  function showErrors(list) {
    const box = document.getElementById("builder-errors");
    box.replaceChildren();
    if (!list || !list.length) { box.classList.add("d-none"); return; }
    box.append(h("strong", { text: "Please fix these problems:" }), h("ul", { class: "mb-0 mt-1" }, list.map((e) => h("li", { text: e }))));
    box.classList.remove("d-none");
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function flattenErrors(payload) {
    if (payload.errors) return payload.errors;
    if (payload.questions) {
      return payload.questions.flatMap((q, i) => (q && typeof q === "object" ? Object.entries(q).map(([k, v]) => `Question ${i + 1} (${k}): ${[].concat(v).join(" ")}`) : []));
    }
    return [payload.detail || "Something went wrong."];
  }

  async function save() {
    if (!editable || saving) return true;
    saving = true;
    setState("", "Saving…");
    const body = { questions: questions.map((q) => ({ ...q, options: q.options.map((o) => (o.oid ? { oid: o.oid, text: o.text } : { text: o.text })) })) };
    const result = await api(data.urls.questions, "PUT", body);
    saving = false;
    if (result.ok) {
      questions = result.payload.questions;
      issues = result.payload.issues;
      dirty = false;
      selected = Math.min(selected, questions.length - 1);
      showErrors([]);
      setState("saved", "All changes saved");
      renderAll();
      return true;
    }
    setState("dirty", "Not saved");
    showErrors(flattenErrors(result.payload));
    return false;
  }

  async function publish() {
    if (dirty && !(await save())) return;
    const result = await api(data.urls.publish, "POST");
    if (result.ok) { window.location.href = data.urls.overview; return; }
    showErrors(flattenErrors(result.payload));
  }

  // ---------------------------------------------------------------- suggestions
  let suggestions = [];
  const resultsEl = document.getElementById("suggest-results");
  const addAllBtn = document.getElementById("suggest-add-all");
  document.getElementById("suggest-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const topic = document.getElementById("suggest-topic").value.trim();
    const objective = document.getElementById("suggest-objective").value.trim();
    resultsEl.replaceChildren(h("p", { class: "muted", text: "Finding suggestions…" }));
    const result = await api(data.urls.suggest, "POST", { topic, objective });
    if (!result.ok) {
      resultsEl.replaceChildren(h("div", { class: "alert alert-danger", text: flattenErrors(result.payload).join(" ") }));
      return;
    }
    suggestions = result.payload.questions;
    document.getElementById("suggest-note").textContent = result.payload.note;
    addAllBtn.disabled = !suggestions.length;
    resultsEl.replaceChildren(...suggestions.map((s, i) => h("div", { class: "suggestion-item" },
      h("div", { class: "flex-grow-1" },
        h("div", { class: "fw-semibold", text: s.title }),
        h("div", { class: "meta", text: [TYPES[s.type].label, s.options.map((o) => o.text).join(", "), s.rationale].filter(Boolean).join(" — ") })),
      h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: (e) => { addQuestion(s.type, s); e.target.closest("button").disabled = true; e.target.closest("button").textContent = "Added"; } }, "Add"),
    )));
  });
  addAllBtn.addEventListener("click", () => {
    suggestions.forEach((s) => addQuestion(s.type, s));
    bootstrap.Modal.getInstance(document.getElementById("suggest-modal")).hide();
  });

  // ---------------------------------------------------------------- start
  const menu = document.getElementById("type-menu");
  data.types.forEach((t) => menu.append(h("button", { type: "button", role: "menuitem", onclick: () => addQuestion(t.key) }, icon(t.icon), t.label)));
  document.querySelectorAll("[data-add-type]").forEach((b) => b.addEventListener("click", () => addQuestion(b.dataset.addType)));
  document.getElementById("save-btn").addEventListener("click", save);
  document.querySelectorAll("form[action$='/publish/']").forEach((form) => form.addEventListener("submit", (event) => {
    if (!editable) return;
    event.preventDefault();
    publish();
  }));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
  });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  if (!editable) {
    document.querySelectorAll("[data-edit-only]").forEach((el) => el.classList.add("d-none"));
    document.getElementById("locked-note").classList.remove("d-none");
    document.getElementById("locked-reason").textContent = !data.canEdit
      ? "Your role on this survey is viewer, so you can look but not change questions."
      : data.status === "archived"
        ? "Archived surveys cannot be edited. Restore it first."
        : "This survey already has responses, and changing questions would make stored answers point to the wrong options. Use “Duplicate as new draft” from the menu to make a new version.";
  }
  renderAll();
})();
