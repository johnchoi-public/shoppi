// shoppi web UI: week calendar + chat with the assistant.
//
// Time handling: the API exchanges *local naive* ISO strings in the calendar's
// configured timezone (e.g. "2026-11-06T15:00:00"), which may differ from the
// browser's timezone. To keep browser DST rules out of the math, wall-clock
// values are held in Date objects *as if they were UTC* and only ever read with
// getUTC*() methods. They are never shown via the browser's local timezone.

"use strict";

const HOUR_PX = 48; // must match --hour-height in style.css
const DAY_MS = 86400000;
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const state = {
  tz: "UTC", // calendar timezone, loaded from /api/config
  weekStart: null, // wall-clock Date (UTC-encoded) of the Sunday that starts the view
  events: [],
  threadId: crypto.randomUUID(), // one agent conversation per page load / "New chat"
  busy: false,
};

const $ = (id) => document.getElementById(id);

// ---- Wall-clock helpers -------------------------------------------------------

/** "2026-11-06T15:00:00" -> Date holding that wall time in its UTC fields. */
function parseWall(s) {
  const [d, t = "00:00:00"] = s.split("T");
  const [y, mo, da] = d.split("-").map(Number);
  const [h, mi, se = 0] = t.split(":").map(Number);
  return new Date(Date.UTC(y, mo - 1, da, h, mi, se));
}

/** Inverse of parseWall, to the second (no offset, no "Z"). */
function formatWall(dt) {
  return dt.toISOString().slice(0, 19);
}

/** Same, at minute precision, for <input type="datetime-local">. */
function formatInput(dt) {
  return dt.toISOString().slice(0, 16);
}

/** Current wall-clock time in the calendar's timezone. */
function nowWall() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: state.tz,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date()).map((p) => [p.type, p.value]),
  );
  return parseWall(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`);
}

function startOfDay(dt) {
  return new Date(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate()));
}

function startOfWeek(dt) {
  return new Date(startOfDay(dt).getTime() - dt.getUTCDay() * DAY_MS);
}

function sameDay(a, b) {
  return startOfDay(a).getTime() === startOfDay(b).getTime();
}

function fmtTime(dt) {
  const h = dt.getUTCHours(), m = dt.getUTCMinutes();
  const suffix = h < 12 ? "am" : "pm";
  const h12 = h % 12 || 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
}

// ---- API ------------------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      // FastAPI validation errors come back as a list of {msg} objects.
      detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

async function loadEvents() {
  const start = state.weekStart;
  const end = new Date(start.getTime() + 7 * DAY_MS);
  const qs = new URLSearchParams({ start: formatWall(start), end: formatWall(end) });
  state.events = await api(`/api/events?${qs}`);
  renderEvents();
}

// ---- Rendering ------------------------------------------------------------------

/** Build the static parts of the week: day headers, hour labels, day columns. */
function renderWeek() {
  const start = state.weekStart;
  const end = new Date(start.getTime() + 6 * DAY_MS);
  const monthFmt = (d) => d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  $("range-label").textContent =
    `${monthFmt(start)} ${start.getUTCDate()} – ${monthFmt(end)} ${end.getUTCDate()}, ${end.getUTCFullYear()}`;

  const today = nowWall();
  const head = $("week-head");
  const days = $("days");
  head.innerHTML = "<div></div>"; // spacer above the hour labels
  days.innerHTML = "";

  for (let i = 0; i < 7; i++) {
    const day = new Date(start.getTime() + i * DAY_MS);

    const h = document.createElement("div");
    h.className = "day-head" + (sameDay(day, today) ? " today" : "");
    h.innerHTML = `<div class="dow">${DOW[i]}</div><div class="date">${day.getUTCDate()}</div>`;
    h.dataset.day = formatWall(day);
    head.appendChild(h);

    const col = document.createElement("div");
    col.className = "day" + (sameDay(day, today) ? " today" : "");
    col.dataset.day = formatWall(day);
    // Clicking empty space creates an event at that half hour.
    col.addEventListener("click", (e) => {
      if (e.target !== col) return;
      const halfHours = Math.floor(e.offsetY / (HOUR_PX / 2));
      const startAt = new Date(day.getTime() + halfHours * 30 * 60000);
      openDialog(null, startAt);
    });
    days.appendChild(col);
  }

  const hours = $("hours");
  hours.innerHTML = "";
  for (let h = 0; h < 24; h++) {
    const label = document.createElement("div");
    label.textContent = h === 0 ? "" : fmtTime(new Date(Date.UTC(2000, 0, 1, h)));
    hours.appendChild(label);
  }
}

/**
 * Place events into day columns. Overlapping events share the column width:
 * each gets a lane, and a cluster of mutually overlapping events splits evenly.
 */
function renderEvents() {
  document.querySelectorAll(".event, .all-day, .now-line").forEach((el) => el.remove());
  const weekStart = state.weekStart.getTime();
  const columns = document.querySelectorAll(".day");
  const heads = document.querySelectorAll(".day-head");

  for (let i = 0; i < 7; i++) {
    const dayStart = weekStart + i * DAY_MS;
    const dayEnd = dayStart + DAY_MS;

    // Clip each event to this day (multi-day events show in every day they touch).
    const items = state.events
      .map((ev) => ({ ev, s: parseWall(ev.start).getTime(), e: parseWall(ev.end).getTime() }))
      .filter(({ s, e }) => s < dayEnd && e > dayStart);

    for (const { ev } of items.filter((x) => x.ev.all_day)) {
      const chip = document.createElement("div");
      chip.className = "all-day";
      chip.textContent = ev.title;
      chip.title = ev.title;
      chip.addEventListener("click", () => openDialog(ev));
      heads[i].appendChild(chip);
    }

    const timed = items
      .filter((x) => !x.ev.all_day)
      .map((x) => ({ ...x, s: Math.max(x.s, dayStart), e: Math.min(x.e, dayEnd) }))
      .sort((a, b) => a.s - b.s || b.e - a.e);

    // Greedy lane assignment within clusters of overlapping events.
    let cluster = [];
    let clusterEnd = -Infinity;
    const flush = () => {
      const lanes = Math.max(...cluster.map((x) => x.lane)) + 1;
      cluster.forEach((x) => (x.lanes = lanes));
      cluster = [];
    };
    for (const item of timed) {
      if (item.s >= clusterEnd && cluster.length) flush();
      const used = new Set(cluster.filter((x) => x.e > item.s).map((x) => x.lane));
      item.lane = 0;
      while (used.has(item.lane)) item.lane++;
      cluster.push(item);
      clusterEnd = Math.max(clusterEnd, item.e);
    }
    if (cluster.length) flush();

    for (const item of timed) {
      const top = ((item.s - dayStart) / 3600000) * HOUR_PX;
      const height = Math.max(((item.e - item.s) / 3600000) * HOUR_PX - 2, 18);
      const el = document.createElement("div");
      el.className = "event";
      el.style.top = `${top}px`;
      el.style.height = `${height}px`;
      el.style.left = `calc(${(item.lane / item.lanes) * 100}% + 2px)`;
      el.style.width = `calc(${100 / item.lanes}% - 4px)`;
      const startDt = parseWall(item.ev.start);
      el.innerHTML = `<div class="time"></div><div class="title"></div>`;
      el.querySelector(".time").textContent = `${fmtTime(startDt)} – ${fmtTime(parseWall(item.ev.end))}`;
      // textContent, not innerHTML: event titles are untrusted (may come from the LLM or imports).
      el.querySelector(".title").textContent = item.ev.title;
      el.title = item.ev.title;
      el.addEventListener("click", () => openDialog(item.ev));
      columns[i].appendChild(el);
    }
  }

  // Red "now" line on today's column.
  const now = nowWall();
  const idx = Math.floor((startOfDay(now).getTime() - weekStart) / DAY_MS);
  if (idx >= 0 && idx < 7) {
    const line = document.createElement("div");
    line.className = "now-line";
    line.style.top = `${((now.getTime() - startOfDay(now).getTime()) / 3600000) * HOUR_PX}px`;
    columns[idx].appendChild(line);
  }
}

// ---- Create / edit dialog -----------------------------------------------------

let editing = null; // event being edited, or null when creating

function openDialog(ev, startAt) {
  editing = ev;
  const form = $("event-form");
  form.reset();
  $("form-error").hidden = true;
  $("dialog-title").textContent = ev ? "Edit event" : "New event";
  $("delete-event").hidden = !ev;

  const start = ev ? parseWall(ev.start) : startAt;
  const end = ev ? parseWall(ev.end) : new Date(start.getTime() + 3600000);
  form.title.value = ev?.title ?? "";
  form.start.value = formatInput(start);
  form.end.value = formatInput(end);
  form.location.value = ev?.location ?? "";
  form.description.value = ev?.description ?? "";
  $("event-dialog").showModal();
  form.title.focus();
}

async function saveEvent(e) {
  e.preventDefault();
  const form = $("event-form");
  const body = {
    title: form.title.value.trim(),
    start: `${form.start.value}:00`,
    end: `${form.end.value}:00`,
    location: form.location.value.trim() || null,
    description: form.description.value.trim() || null,
  };
  try {
    if (editing) {
      await api(`/api/events/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) });
    } else {
      await api("/api/events", { method: "POST", body: JSON.stringify(body) });
    }
    $("event-dialog").close();
    await loadEvents();
  } catch (err) {
    $("form-error").textContent = err.message;
    $("form-error").hidden = false;
  }
}

async function deleteEvent() {
  if (!editing || !confirm(`Delete "${editing.title}"?`)) return;
  try {
    await api(`/api/events/${editing.id}`, { method: "DELETE" });
    $("event-dialog").close();
    await loadEvents();
  } catch (err) {
    $("form-error").textContent = err.message;
    $("form-error").hidden = false;
  }
}

// ---- Chat -----------------------------------------------------------------------

function addMessage(kind, text) {
  const hint = document.querySelector(".hint");
  if (hint) hint.remove();
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  el.textContent = text;
  $("messages").appendChild(el);
  $("messages").scrollTop = $("messages").scrollHeight;
  return el;
}

function describeToolCall(name, args) {
  const brief = Object.entries(args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
  return `→ ${name}(${brief})`;
}

/** Send a message and render the agent's SSE stream as it arrives. */
async function sendChat(e) {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("send").disabled = true;
  $("input").value = "";
  addMessage("user", text);

  let reply = null; // assistant bubble currently receiving tokens
  let pending = ""; // text streamed since the last tool call
  let calendarTouched = false;
  try {
    // EventSource only supports GET, so read the POST response body as a stream.
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, thread_id: state.threadId }),
    });
    if (!res.ok) throw new Error(`chat failed: ${res.status}`);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      // SSE messages are separated by a blank line.
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (!raw.startsWith("data: ")) continue;
        const ev = JSON.parse(raw.slice(6));
        if (ev.type === "token") {
          // Models often stream whitespace before deciding to call a tool; hold
          // tokens until there is visible text so no empty bubble appears.
          pending += ev.text;
          if (!pending.trim()) continue;
          reply ??= addMessage("assistant", "");
          reply.textContent = pending.trimStart();
          $("messages").scrollTop = $("messages").scrollHeight;
        } else if (ev.type === "tool_call") {
          reply = null; // text after a tool call starts a new bubble
          pending = "";
          addMessage("tool", describeToolCall(ev.name, ev.args));
          if (ev.name !== "list_events" && ev.name !== "search_documents") calendarTouched = true;
        } else if (ev.type === "error") {
          addMessage("error", ev.message);
        }
      }
    }
  } catch (err) {
    addMessage("error", err.message);
  } finally {
    state.busy = false;
    $("send").disabled = false;
    $("input").focus();
    // Show the agent's changes on the calendar.
    if (calendarTouched) loadEvents();
  }
}

// ---- Wiring ---------------------------------------------------------------------

function goToWeek(weekStart) {
  state.weekStart = weekStart;
  renderWeek();
  loadEvents().catch((err) => addMessage("error", `Could not load events: ${err.message}`));
}

async function init() {
  state.tz = (await api("/api/config")).timezone;
  $("tz-label").textContent = state.tz;

  $("prev").addEventListener("click", () => goToWeek(new Date(state.weekStart.getTime() - 7 * DAY_MS)));
  $("next").addEventListener("click", () => goToWeek(new Date(state.weekStart.getTime() + 7 * DAY_MS)));
  $("today").addEventListener("click", () => goToWeek(startOfWeek(nowWall())));
  $("new-event").addEventListener("click", () => {
    // Default new events to the next full hour.
    const now = nowWall();
    openDialog(null, new Date(Math.ceil(now.getTime() / 3600000) * 3600000));
  });
  $("event-form").addEventListener("submit", saveEvent);
  $("cancel").addEventListener("click", () => $("event-dialog").close());
  $("delete-event").addEventListener("click", deleteEvent);
  $("composer").addEventListener("submit", sendChat);
  $("input").addEventListener("keydown", (e) => {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("new-chat").addEventListener("click", () => {
    state.threadId = crypto.randomUUID();
    $("messages").innerHTML = "";
  });

  goToWeek(startOfWeek(nowWall()));
  // Start scrolled to 8am rather than midnight.
  $("grid-scroll").scrollTop = 8 * HOUR_PX;
  // Keep the "now" line moving.
  setInterval(renderEvents, 60000);
}

init();
