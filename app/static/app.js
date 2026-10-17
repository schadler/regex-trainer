/* Regex-Trainer Frontend */
"use strict";

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const showWs = (s) => (s === "" ? "(leer)" : esc(s).replace(/\n/g, "<span class='ws'>↵</span>\n"));

async function postJSON(url, data) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  let body = {};
  try { body = await r.json(); } catch (e) { body = { error: "Serverfehler (" + r.status + ")" }; }
  return body;
}

const Store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* egal */ } },
};

/* ---------------------------------------------------------- Fortschritt */
const Progress = {
  done() { return new Set(Store.get("regex-done", [])); },
  mark(id) { const d = this.done(); d.add(id); Store.set("regex-done", [...d]); },
  renderIndex() {
    const done = this.done();
    let all = 0, ok = 0;
    document.querySelectorAll(".badge[data-ex]").forEach((b) => {
      const ids = b.dataset.ex.split(",").filter(Boolean);
      const n = ids.filter((i) => done.has(i)).length;
      all += ids.length; ok += n;
      b.textContent = n + "/" + ids.length;
      b.classList.toggle("complete", n === ids.length && n > 0);
    });
    const bar = document.getElementById("progress-bar");
    if (bar) {
      bar.style.width = (all ? (ok / all) * 100 : 0) + "%";
      document.getElementById("progress-text").textContent = ok + " von " + all + " Übungen gelöst";
    }
  },
};

/* ---------------------------------------------------------- Ergebnisanzeige */
function renderResult(box, res, section) {
  section.querySelectorAll(".case[data-case]").forEach((c) => c.classList.remove("ok", "bad"));
  if (res.error) {
    box.innerHTML = `<div class="error">${esc(res.error)}</div>`;
    return;
  }
  const all = res.passed === res.total;
  let html = `<div class="summary ${all ? "ok" : "bad"}">${all ? "✓ Alles richtig!" : "✗ " + res.passed + " von " + res.total + " richtig"}</div>`;
  let mi = 0, ni = 0;
  const lines = [];
  res.results.forEach((r) => {
    if (r.kind === "match") {
      const key = r.expect ? "m" + mi++ : "n" + ni++;
      const chip = section.querySelector(`.case[data-case="${key}"]`);
      if (chip) chip.classList.add(r.ok ? "ok" : "bad");
      if (!r.ok) {
        lines.push(`<li><code>${showWs(r.text)}</code> ${r.expect ? "sollte passen, passt aber nicht" : "sollte nicht passen, passt aber"}${r.note ? " – " + esc(r.note) : ""}</li>`);
      }
    } else {
      const fmt = (a) => (a === null || a === undefined ? "–" : "[" + a.map((x) => `<code>${esc(x === null ? "None" : x)}</code>`).join(", ") + "]");
      html += `<div class="fa-cmp"><div><span class="muted">erwartet</span> ${fmt(r.expected)}</div><div><span class="muted">dein Ergebnis</span> ${fmt(r.got)}</div>${r.note ? `<div class="error">${esc(r.note)}</div>` : ""}</div>`;
    }
  });
  if (lines.length) html += `<ul class="fails">${lines.join("")}</ul>`;
  box.innerHTML = html;
}

/* ---------------------------------------------------------- Lernmodus */
const Exercise = {
  init() {
    const done = Progress.done();
    document.querySelectorAll(".exercise").forEach((s) => {
      if (done.has(s.dataset.id)) s.querySelector(".done-mark").hidden = false;
      const saved = Store.get("regex-pat-" + s.dataset.id, "");
      if (saved) s.querySelector("input[name=pattern]").value = saved;
    });
  },
  async check(form) {
    const section = form.closest(".exercise");
    const id = section.dataset.id;
    const pattern = form.pattern.value;
    Store.set("regex-pat-" + id, pattern);
    const res = await postJSON("/api/check", { id, pattern });
    renderResult(section.querySelector(".result"), res, section);
    if (!res.error && res.passed === res.total) {
      Progress.mark(id);
      section.querySelector(".done-mark").hidden = false;
    }
  },
  async solution(btn) {
    const section = btn.closest(".exercise");
    const r = await fetch("/api/solution/" + section.dataset.id).then((x) => x.json());
    const inp = section.querySelector("input[name=pattern]");
    inp.value = r.solution;
    inp.focus();
  },
  toPlayground(a) {
    const section = a.closest(".exercise");
    const pat = section.querySelector("input[name=pattern]").value;
    const sample = section.querySelector(".text-sample");
    let text = sample ? sample.textContent : [...section.querySelectorAll(".case[data-case]")].map((c) => c.textContent).join("\n");
    location.href = "/spielwiese#" + new URLSearchParams({ p: pat, t: text }).toString();
  },
};

/* ---------------------------------------------------------- Spielwiese */
const Playground = {
  init() {
    this.p = document.getElementById("pg-pattern");
    this.t = document.getElementById("pg-text");
    if (location.hash.length > 1) {
      const q = new URLSearchParams(location.hash.slice(1));
      if (q.has("p")) this.p.value = q.get("p");
      if (q.has("t")) this.t.value = q.get("t");
    } else {
      this.p.value = Store.get("pg-p", "\\d+");
      this.t.value = Store.get("pg-t", this.t.value);
    }
    let timer;
    const go = () => { clearTimeout(timer); timer = setTimeout(() => this.run(), 200); };
    this.p.addEventListener("input", go);
    this.t.addEventListener("input", go);
    document.querySelectorAll(".flags input").forEach((c) => c.addEventListener("change", go));
    this.run();
  },
  flags() { return [...document.querySelectorAll(".flags input:checked")].map((c) => c.value); },
  async run() {
    const pattern = this.p.value, text = this.t.value, flags = this.flags();
    Store.set("pg-p", pattern); Store.set("pg-t", text);
    const err = document.getElementById("pg-error");
    const hl = document.getElementById("pg-highlight");
    const tbl = document.getElementById("pg-table");
    const cnt = document.getElementById("pg-count");
    this.python(pattern, flags);
    if (!pattern) { err.hidden = true; hl.innerHTML = esc(text); tbl.innerHTML = ""; cnt.textContent = ""; return; }
    const res = await postJSON("/api/match", { pattern, text, flags });
    if (!res.ok) {
      err.hidden = false; err.textContent = res.error;
      hl.innerHTML = esc(text); tbl.innerHTML = ""; cnt.textContent = "";
      return;
    }
    err.hidden = true;
    const ms = res.matches;
    cnt.textContent = ms.length + (ms.length === 1 ? " Treffer" : " Treffer");
    let out = "", pos = 0;
    ms.forEach((m, i) => {
      out += esc(text.slice(pos, m.start));
      out += m.start === m.end
        ? `<span class="zw" title="leerer Treffer an Position ${m.start}"></span>`
        : `<mark class="m${i % 2}" title="Treffer ${i + 1}">${esc(text.slice(m.start, m.end))}</mark>`;
      pos = m.end;
    });
    out += esc(text.slice(pos));
    hl.innerHTML = out || "&nbsp;";
    if (!ms.length) { tbl.innerHTML = `<p class="muted">Kein Treffer.</p>`; return; }
    const ng = ms[0].groups.length;
    let h = `<div class="scroll-x"><table class="tbl small"><tr><th>#</th><th>Treffer</th><th>Position</th>`;
    ms[0].groups.forEach((g) => { h += `<th>Gruppe ${g.nr}${g.name ? " <small>(" + esc(g.name) + ")</small>" : ""}</th>`; });
    h += `</tr>`;
    ms.slice(0, 200).forEach((m, i) => {
      h += `<tr><td>${i + 1}</td><td><code>${showWs(m.text)}</code></td><td>${m.start}–${m.end}</td>`;
      m.groups.forEach((g) => { h += `<td>${g.text === null ? '<span class="muted">None</span>' : "<code>" + showWs(g.text) + "</code>"}</td>`; });
      h += `</tr>`;
    });
    h += `</table></div>`;
    if (ng === 0) h += `<p class="muted small">Tipp: Mit runden Klammern ( ) kannst du Teile als Gruppen auslesen.</p>`;
    tbl.innerHTML = h;
  },
  python(pattern, flags) {
    const fl = flags.length ? ", " + flags.map((f) => "re." + f).join(" | ") : "";
    const quote = pattern.includes('"') ? "'" : '"';
    document.getElementById("pg-python").textContent =
      `import re\n\npattern = re.compile(r${quote}${pattern}${quote}${fl})\nfor m in pattern.finditer(text):\n    print(m.group(0), m.span())`;
  },
};

/* ---------------------------------------------------------- Test */
const TestRun = {
  init() {
    this.form = document.getElementById("test-form");
    const t = document.getElementById("timer");
    this.end = Date.now() + parseInt(t.dataset.remaining, 10) * 1000;
    this.tick();
    this.iv = setInterval(() => this.tick(), 1000);
    let timer;
    this.form.addEventListener("input", () => {
      document.getElementById("save-state").textContent = "ungespeichert …";
      clearTimeout(timer);
      timer = setTimeout(() => this.save(), 1200);
    });
    this.form.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName === "INPUT") {
        e.preventDefault();
        this.tryTask(e.target);
      }
    });
    this.submitting = false;
    this.form.addEventListener("submit", () => { this.submitting = true; });
  },
  tick() {
    const left = Math.max(0, Math.round((this.end - Date.now()) / 1000));
    const el = document.getElementById("timer");
    const mm = Math.floor(left / 60), ss = left % 60;
    el.textContent = String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0");
    el.classList.toggle("low", left < 300);
    if (left <= 0 && !this.submitting) {
      clearInterval(this.iv);
      this.submitting = true;
      el.textContent = "Zeit abgelaufen";
      this.form.submit();
    }
  },
  answers() {
    const a = {};
    this.form.querySelectorAll(".test-task").forEach((s) => { a[s.dataset.id] = s.querySelector("input").value; });
    return a;
  },
  async save() {
    const st = document.getElementById("save-state");
    const r = await postJSON("/api/test/save", { answers: this.answers() });
    if (r.done) { location.href = "/test/fertig"; return; }
    st.textContent = r.ok ? "gespeichert " + r.saved : "Speichern fehlgeschlagen!";
    st.classList.toggle("bad", !r.ok);
  },
  async tryTask(el) {
    const section = el.closest(".test-task");
    const pattern = section.querySelector("input").value;
    const res = await postJSON("/api/test/try", { id: section.dataset.id, pattern });
    if (res.done) { location.href = "/test/fertig"; return; }
    renderResult(section.querySelector(".result"), res, section);
    const box = section.querySelector(".result");
    if (!res.error) box.insertAdjacentHTML("beforeend", `<p class="muted small">Nur sichtbare Beispiele – die Bewertung verwendet zusätzlich versteckte Fälle.</p>`);
    document.getElementById("save-state").textContent = "gespeichert";
  },
};
