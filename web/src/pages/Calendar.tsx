// The built-in shop calendar: month / week / day / agenda, plus by-mechanic and
// by-bay resource views. Jobs are dragged to reschedule; every move is checked by
// the scheduler on the server, so a drop can be refused with the reason.
import { useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useCan, useMe, useNow, useQ, useRun } from "../store";
import { addDays, addMinutes, cx, fmtDay, fmtRange, fmtTime, fromInput, iso, isoDate, minutesOfDay, parse, sameDay, startOfDay, startOfMonth, startOfWeek, toInput, WEEKDAYS } from "../lib";
import { AccessBanner, EntityHistory, useScheduleCommand } from "../parts/shared";
import { Badge, Button, Checkbox, Drawer, Field, Input, Loading, Modal, PageHeader, Select, StatusBadge, Textarea, useForm } from "../ui";

const PX_PER_HOUR = 56;
const SNAP = 15;

const FILTERS: [string, string, string[]][] = [
  ["jobs", "Jobs", ["job", "hold"]],
  ["events", "Events", ["event"]],
  ["shifts", "Shifts", ["shift"]],
  ["time_off", "Time off", ["time_off"]],
  ["closures", "Bay closures", ["bay_closure"]],
  ["deliveries", "Parts deliveries", ["delivery"]],
  ["certs", "Cert expiries", ["cert_expiry"]],
  ["promised", "Promise times", ["promised"]],
];

function itemStyle(i: any): string {
  if (i.kind === "job") {
    if (i.status === "needs_reassignment") return "border-red-400 bg-red-50 text-red-900";
    if (i.status === "in_progress") return "border-emerald-500 bg-emerald-50 text-emerald-900";
    if (i.status === "paused") return "border-amber-400 bg-amber-50 text-amber-900";
    if (i.status === "done") return "border-slate-300 bg-slate-100 text-slate-500";
    return "border-brand-500 bg-brand-50 text-brand-900";
  }
  return {
    hold: "border-slate-300 bg-[repeating-linear-gradient(45deg,#f1f5f9,#f1f5f9_6px,#e2e8f0_6px,#e2e8f0_12px)] text-slate-600",
    event: "border-violet-400 bg-violet-50 text-violet-900",
    shift: "border-lime-300 bg-lime-50 text-lime-900",
    time_off: "border-slate-300 bg-slate-200 text-slate-700",
    bay_closure: "border-rose-300 bg-rose-50 text-rose-900",
    delivery: "border-sky-400 bg-sky-50 text-sky-900",
    cert_expiry: "border-amber-400 bg-amber-50 text-amber-900",
    promised: "border-orange-400 bg-orange-50 text-orange-900",
  }[i.kind as string] ?? "border-slate-300 bg-white";
}

type View = "month" | "week" | "day" | "agenda";
type Resource = "none" | "mechanic" | "bay";

export function CalendarPage() {
  const me = useMe();
  const can = useCan();
  const now = useNow();
  const [params, setParams] = useSearchParams();
  const view = (params.get("view") as View) || "week";
  const resource = (params.get("by") as Resource) || "none";
  const cursor = params.get("date") ? parse(params.get("date")! + "T00:00:00") : startOfDay(now);
  const [filters, setFilters] = useState<Record<string, boolean>>({ jobs: true, events: true, shifts: false, time_off: true, closures: true, deliveries: true, certs: true, promised: true });
  const [selected, setSelected] = useState<any | null>(null);
  const [creating, setCreating] = useState<any | null>(null);
  const run = useRun();
  const sched = useScheduleCommand();

  const go = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) next.set(k, v);
    setParams(next, { replace: true });
  };

  const [rangeStart, rangeEnd] = useMemo(() => {
    if (view === "month") { const s = startOfWeek(startOfMonth(cursor)); return [s, addDays(s, 42)]; }
    if (view === "week") { const s = startOfWeek(cursor); return [s, addDays(s, 7)]; }
    if (view === "agenda") return [cursor, addDays(cursor, 14)];
    return [cursor, addDays(cursor, 1)];
  }, [view, cursor.getTime()]);

  const kinds = FILTERS.filter(([key]) => filters[key]).flatMap(([, , k]) => k);
  // Resource views shade working hours, so they always need shifts and closures.
  const fetchKinds = resource !== "none" && view === "day" ? Array.from(new Set([...kinds, "shift", "time_off", "bay_closure"])) : kinds;
  const feed = useQ<any[]>("calendar.feed", { start: iso(rangeStart), end: iso(rangeEnd), kinds: fetchKinds.length ? fetchKinds : ["none"] });
  const employees = useQ<any[]>("employees.list", {}, { enabled: can.view("team") });
  const board = useQ<any[]>("bays.board", { day: isoDate(cursor) }, { enabled: can.view("bays") && resource === "bay" && view === "day" });
  const baysList = useQ<any>("bays.list", {}, { enabled: can.view("bays") });
  const items = (feed.data ?? []).filter((i) => kinds.includes(i.kind));
  const all = feed.data ?? [];

  const shift = (n: number) => go({ date: isoDate(view === "month" ? new Date(cursor.getFullYear(), cursor.getMonth() + n, 1) : addDays(cursor, n * (view === "week" ? 7 : view === "agenda" ? 14 : 1))) });
  const title = view === "month" ? cursor.toLocaleDateString("en-US", { month: "long", year: "numeric" })
    : view === "day" ? fmtDay(cursor, { weekday: "long", month: "long", day: "numeric", year: "numeric" })
    : `${fmtDay(rangeStart, { month: "short", day: "numeric" })} – ${fmtDay(addDays(rangeEnd, -1), { month: "short", day: "numeric", year: "numeric" })}`;

  async function drop(item: any, start: Date, column: any) {
    const length = (parse(item.end).getTime() - parse(item.start).getTime()) / 60000;
    if (item.kind === "job") {
      const args: any = { id: item.ref_id, start: iso(start) };
      if (column.employeeId) args.employee_id = column.employeeId;
      if (column.bayId) args.bay_id = column.bayId;
      await sched.run("schedule.move", args, "Job moved.");
    } else if (item.kind === "event") {
      const args: any = { id: item.ref_id, start: iso(start), end: iso(addMinutes(start, length)) };
      if (column.employeeId) args.employee_id = column.employeeId;
      if (column.bayId) args.bay_id = column.bayId;
      await run("events.update", args, { ok: "Event moved." });
    }
  }

  // Columns for the time grid
  const mechanics = (employees.data ?? []).filter((e) => e.active && (e.kind === "tech" || e.kind === "apprentice"));
  const bays = (baysList.data?.bays ?? []).filter((b: any) => b.active);
  let columns: any[] = [];
  if (view === "week") columns = Array.from({ length: 7 }, (_, i) => ({ key: String(i), date: addDays(rangeStart, i), label: fmtDay(addDays(rangeStart, i), { weekday: "short", day: "numeric" }) }));
  else if (view === "day" && resource === "mechanic") {
    columns = mechanics.map((m) => ({
      key: m.id, date: cursor, label: m.name, sub: m.kind === "apprentice" ? "Apprentice" : `Level ${m.skill_level}`, employeeId: m.id,
      working: all.filter((i) => i.kind === "shift" && i.employee_id === m.id).map((i) => [parse(i.start), parse(i.end)]),
    }));
  } else if (view === "day" && resource === "bay") {
    columns = bays.map((b: any) => ({
      key: b.id, date: cursor, label: b.name, sub: `up to class ${b.max_gvwr_class}`, bayId: b.id,
      working: (board.data?.find((x) => x.bay.id === b.id)?.open_today ?? []).map((o: any) => [parse(o.start), parse(o.end)]),
    }));
  } else if (view === "day") columns = [{ key: "day", date: cursor, label: fmtDay(cursor, { weekday: "long", month: "short", day: "numeric" }) }];

  const hours = useMemo(() => {
    const opens = Object.values(me.shop.hours ?? {}).filter(Boolean) as string[][];
    const first = Math.min(8, ...opens.map((h) => Number(h[0].slice(0, 2))));
    const last = Math.max(17, ...opens.map((h) => Number(h[1].slice(0, 2)) + (h[1].slice(3) === "00" ? 0 : 1)));
    return [Math.max(0, first - 1), Math.min(24, last + 1)];
  }, [me.shop.hours]);

  return (
    <div className="flex h-full flex-col">
      {sched.modal}
      <PageHeader title="Calendar" subtitle={`All times are ${me.shop.timezone.replace(/_/g, " ")} time.`}
        actions={<Button variant="primary" onClick={() => setCreating({ start: iso(new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate(), 9)), end: iso(new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate(), 10)) })}>+ New event</Button>} />
      <AccessBanner vertical="schedule" />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-md border border-slate-300">
          <button onClick={() => shift(-1)} className="bg-white px-2.5 py-1.5 hover:bg-slate-50" aria-label="Previous">‹</button>
          <button onClick={() => go({ date: isoDate(now) })} className="border-x border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50">Today</button>
          <button onClick={() => shift(1)} className="bg-white px-2.5 py-1.5 hover:bg-slate-50" aria-label="Next">›</button>
        </div>
        <h2 className="mr-auto text-base font-semibold text-slate-900">{title}</h2>
        {view === "day" && (
          <Segmented value={resource} onChange={(v) => go({ by: v })} options={[["none", "All"], ...(can.view("team") ? [["mechanic", "By mechanic"]] as any : []), ...(can.view("bays") ? [["bay", "By bay"]] as any : [])]} />
        )}
        <Segmented value={view} onChange={(v) => go({ view: v })} options={[["month", "Month"], ["week", "Week"], ["day", "Day"], ["agenda", "Agenda"]]} />
      </div>

      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1">
        {FILTERS.map(([key, label]) => <Checkbox key={key} label={label} checked={!!filters[key]} onChange={(e) => setFilters((f) => ({ ...f, [key]: (e.target as HTMLInputElement).checked }))} />)}
      </div>

      <div className="card min-h-0 flex-1 overflow-auto">
        {feed.isLoading ? <Loading />
          : view === "month" ? <MonthGrid start={rangeStart} cursor={cursor} now={now} items={items} onDay={(d) => go({ view: "day", date: isoDate(d) })} onItem={setSelected} />
          : view === "agenda" ? <Agenda start={rangeStart} days={14} items={items} onItem={setSelected} />
          : columns.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">{resource === "mechanic" ? "No mechanics yet. Add them under Team." : "No bays yet. Add them under Settings → Bays."}</div>
          : <TimeGrid columns={columns} items={items} hours={hours} now={now} onItem={setSelected} onDrop={drop}
              onCreate={(start, col) => setCreating({ start: iso(start), end: iso(addMinutes(start, 60)), employee_id: col.employeeId ?? "", bay_id: col.bayId ?? "" })} />}
      </div>

      {selected && <ItemDrawer item={selected} onClose={() => setSelected(null)} employees={employees.data ?? []} bays={bays} />}
      {creating && <EventModal initial={creating} onClose={() => setCreating(null)} employees={employees.data ?? []} bays={bays} />}
    </div>
  );
}

function Segmented({ value, onChange, options }: { value: string; onChange: (v: any) => void; options: [string, string][] }) {
  return (
    <div className="flex overflow-hidden rounded-md border border-slate-300" role="group">
      {options.map(([v, label], i) => (
        <button key={v} onClick={() => onChange(v)} aria-pressed={value === v}
          className={cx("px-3 py-1.5 text-sm", i > 0 && "border-l border-slate-300", value === v ? "bg-brand-700 font-medium text-white" : "bg-white text-slate-700 hover:bg-slate-50")}>{label}</button>
      ))}
    </div>
  );
}

// ---- Time grid (week, day, by mechanic, by bay) -----------------------------------

function belongs(item: any, col: any) {
  if (col.employeeId) return item.employee_id === col.employeeId && item.kind !== "shift";
  if (col.bayId) return item.bay_id === col.bayId;
  return true;
}

function TimeGrid({ columns, items, hours, now, onItem, onDrop, onCreate }: {
  columns: any[]; items: any[]; hours: number[]; now: Date; onItem: (i: any) => void;
  onDrop: (item: any, start: Date, column: any) => void; onCreate: (start: Date, column: any) => void;
}) {
  const [h0, h1] = hours;
  const height = (h1 - h0) * PX_PER_HOUR;
  const dragging = useRef<{ item: any; offset: number } | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const timeAt = (e: React.MouseEvent, col: any, offset = 0) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const minutes = h0 * 60 + ((e.clientY - rect.top) / PX_PER_HOUR) * 60 - offset;
    const snapped = Math.max(0, Math.min(24 * 60 - SNAP, Math.round(minutes / SNAP) * SNAP));
    return addMinutes(startOfDay(col.date), snapped);
  };

  return (
    <div className="flex min-w-max">
      <div className="sticky left-0 z-20 w-14 shrink-0 border-r border-slate-200 bg-white">
        <div className="h-12 border-b border-slate-200" />
        <div className="relative" style={{ height }}>
          {Array.from({ length: h1 - h0 }, (_, i) => (
            <div key={i} className="absolute right-1.5 -translate-y-1/2 text-[11px] text-slate-400" style={{ top: i * PX_PER_HOUR }}>{i > 0 && fmtTime(new Date(2000, 0, 1, h0 + i))}</div>
          ))}
        </div>
      </div>
      {columns.map((col) => {
        const dayStart = startOfDay(col.date), dayEnd = addDays(dayStart, 1);
        const mine = items.filter((i) => belongs(i, col) && parse(i.start) < dayEnd && parse(i.end) > dayStart);
        // In a plain day/week column a whole-day item (time off, a long closure) would bury everything else: show it as a header chip.
        const whole = (i: any) => i.all_day || (!col.employeeId && !col.bayId && parse(i.start) <= dayStart && parse(i.end) >= dayEnd);
        const allDay = mine.filter(whole);
        const timed = layout(mine.filter((i) => !whole(i)), dayStart, dayEnd);
        const isToday = sameDay(col.date, now);
        return (
          <div key={col.key} className="min-w-36 flex-1 border-r border-slate-200 last:border-r-0">
            <div className={cx("sticky top-0 z-10 flex h-12 flex-col justify-center border-b border-slate-200 px-2", isToday && !col.employeeId && !col.bayId ? "bg-brand-50" : "bg-white")}>
              <div className="truncate text-sm font-semibold text-slate-800">{col.label}</div>
              {col.sub && <div className="truncate text-[11px] text-slate-500">{col.sub}</div>}
              {allDay.length > 0 && <div className="truncate text-[11px] text-amber-700" title={allDay.map((a) => a.title).join("\n")}>{allDay[0].title}{allDay.length > 1 && ` +${allDay.length - 1}`}</div>}
            </div>
            <div
              className={cx("relative", col.working ? "bg-slate-100" : "bg-white", over === col.key && "ring-2 ring-inset ring-brand-500")}
              style={{ height }}
              onDragOver={(e) => { if (dragging.current) { e.preventDefault(); setOver(col.key); } }}
              onDragLeave={() => setOver((o) => (o === col.key ? null : o))}
              onDrop={(e) => {
                e.preventDefault();
                setOver(null);
                const d = dragging.current;
                dragging.current = null;
                if (d) onDrop(d.item, timeAt(e, col, d.offset), col);
              }}
              onDoubleClick={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).dataset.bg) onCreate(timeAt(e, col), col); }}
              title="Double-click to add an event"
            >
              {/* Working hours (shift or bay opening hours) are white; the rest is shaded. */}
              {(col.working ?? []).map(([s, e]: Date[], i: number) => {
                const top = Math.max(0, (minutesOfDay(s) - h0 * 60) / 60 * PX_PER_HOUR);
                const bottom = Math.min(height, ((sameDay(e, col.date) ? minutesOfDay(e) : 24 * 60) - h0 * 60) / 60 * PX_PER_HOUR);
                return bottom > top && <div key={i} data-bg="1" className="absolute inset-x-0 bg-white" style={{ top, height: bottom - top }} />;
              })}
              {Array.from({ length: h1 - h0 }, (_, i) => <div key={i} data-bg="1" className="pointer-events-none absolute inset-x-0 border-t border-slate-200/70" style={{ top: i * PX_PER_HOUR }} />)}
              {isToday && minutesOfDay(now) >= h0 * 60 && minutesOfDay(now) <= h1 * 60 && (
                <div className="pointer-events-none absolute inset-x-0 z-10 border-t-2 border-red-500" style={{ top: (minutesOfDay(now) - h0 * 60) / 60 * PX_PER_HOUR }} />
              )}
              {timed.map(({ item, startMin, endMin, lane, lanes }) => {
                const top = Math.max(0, (startMin - h0 * 60) / 60 * PX_PER_HOUR);
                const bottom = Math.min(height, (endMin - h0 * 60) / 60 * PX_PER_HOUR);
                if (bottom <= top) return null;
                const background = ["shift", "time_off", "bay_closure", "hold"].includes(item.kind);
                return (
                  <button
                    key={item.id}
                    draggable={!!item.editable}
                    onDragStart={(e) => {
                      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                      dragging.current = { item, offset: ((e.clientY - rect.top) / PX_PER_HOUR) * 60 };
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", item.id);
                    }}
                    onDragEnd={() => { dragging.current = null; setOver(null); }}
                    onClick={() => onItem(item)}
                    className={cx("absolute overflow-hidden rounded border-l-4 px-1.5 py-0.5 text-left text-xs leading-tight shadow-xs hover:z-20 hover:shadow-md",
                      itemStyle(item), item.editable && "cursor-grab active:cursor-grabbing", background ? "opacity-80" : "z-[5]")}
                    style={{ top: top + 1, height: Math.max(18, bottom - top - 2), left: `calc(${(lane / lanes) * 100}% + 2px)`, width: `calc(${100 / lanes}% - 4px)` }}
                    title={`${item.title}\n${fmtRange(item.start, item.end)}${item.problem ? "\n⚠ " + item.problem : ""}`}
                  >
                    <div className="truncate font-semibold">{item.status === "needs_reassignment" && "⚠ "}{item.title}</div>
                    {bottom - top > 34 && <div className="truncate opacity-80">{fmtTime(item.start)}–{fmtTime(item.end)}{!col.employeeId && item.employee_name ? ` · ${item.employee_name}` : ""}{!col.bayId && item.bay_name ? ` · ${item.bay_name}` : ""}</div>}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Put overlapping items side by side. */
function layout(items: any[], dayStart: Date, dayEnd: Date) {
  const rows = items.map((item) => {
    const s = parse(item.start), e = parse(item.end);
    return { item, startMin: s < dayStart ? 0 : minutesOfDay(s), endMin: e >= dayEnd ? 24 * 60 : minutesOfDay(e), lane: 0, lanes: 1 };
  }).sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin);
  let cluster: typeof rows = [];
  let clusterEnd = -1;
  const flush = () => { const n = Math.max(1, ...cluster.map((r) => r.lane + 1)); cluster.forEach((r) => (r.lanes = n)); cluster = []; };
  for (const row of rows) {
    if (cluster.length && row.startMin >= clusterEnd) { flush(); clusterEnd = -1; }
    const used = new Set(cluster.filter((r) => r.endMin > row.startMin).map((r) => r.lane));
    let lane = 0;
    while (used.has(lane)) lane++;
    row.lane = lane;
    cluster.push(row);
    clusterEnd = Math.max(clusterEnd, row.endMin);
  }
  flush();
  return rows;
}

// ---- Month and agenda -------------------------------------------------------------

function MonthGrid({ start, cursor, now, items, onDay, onItem }: { start: Date; cursor: Date; now: Date; items: any[]; onDay: (d: Date) => void; onItem: (i: any) => void }) {
  return (
    <div className="grid h-full min-h-[560px] grid-cols-7 grid-rows-[auto_repeat(6,minmax(0,1fr))]">
      {WEEKDAYS.map((d) => <div key={d} className="border-b border-slate-200 bg-slate-50 px-2 py-1.5 text-xs font-semibold uppercase text-slate-500">{d}</div>)}
      {Array.from({ length: 42 }, (_, i) => {
        const day = addDays(start, i), next = addDays(day, 1);
        const mine = items.filter((it) => parse(it.start) < next && parse(it.end) > day && it.kind !== "shift" && it.kind !== "hold");
        return (
          <div key={i} className={cx("min-h-0 overflow-hidden border-b border-r border-slate-200 p-1", day.getMonth() !== cursor.getMonth() && "bg-slate-50/70")}>
            <button onClick={() => onDay(day)} className={cx("mb-0.5 flex h-6 w-6 items-center justify-center rounded-full text-xs hover:bg-slate-200", sameDay(day, now) ? "bg-brand-700 font-semibold text-white hover:bg-brand-800" : "text-slate-600")}>{day.getDate()}</button>
            <div className="space-y-0.5">
              {mine.slice(0, 3).map((it) => (
                <button key={it.id} onClick={() => onItem(it)} className={cx("block w-full truncate rounded border-l-2 px-1 text-left text-[11px]", itemStyle(it))}>
                  {!it.all_day && <span className="opacity-70">{fmtTime(it.start)} </span>}{it.title}
                </button>
              ))}
              {mine.length > 3 && <button onClick={() => onDay(day)} className="px-1 text-[11px] text-slate-500 hover:underline">+{mine.length - 3} more</button>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Agenda({ start, days, items, onItem }: { start: Date; days: number; items: any[]; onItem: (i: any) => void }) {
  const groups = Array.from({ length: days }, (_, i) => addDays(start, i)).map((day) => ({
    day, list: items.filter((it) => sameDay(parse(it.start), day) && it.kind !== "shift"),
  })).filter((g) => g.list.length);
  if (!groups.length) return <div className="p-8 text-center text-sm text-slate-500">Nothing scheduled in the next two weeks.</div>;
  return (
    <div className="divide-y divide-slate-100">
      {groups.map(({ day, list }) => (
        <div key={isoDate(day)} className="flex gap-4 px-4 py-3">
          <div className="w-28 shrink-0 text-sm font-semibold text-slate-700">{fmtDay(day)}</div>
          <ul className="flex-1 space-y-1">
            {list.map((it) => (
              <li key={it.id}>
                <button onClick={() => onItem(it)} className={cx("flex w-full items-center gap-3 rounded border-l-4 px-2 py-1 text-left text-sm hover:shadow-sm", itemStyle(it))}>
                  <span className="w-28 shrink-0 text-xs opacity-80">{it.all_day ? "All day" : `${fmtTime(it.start)} – ${fmtTime(it.end)}`}</span>
                  <span className="flex-1 truncate font-medium">{it.title}</span>
                  <span className="text-xs opacity-80">{[it.employee_name, it.bay_name].filter(Boolean).join(" · ")}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

// ---- Item drawer and event form ---------------------------------------------------

function ItemDrawer({ item, onClose, employees, bays }: { item: any; onClose: () => void; employees: any[]; bays: any[] }) {
  const me = useMe();
  const can = useCan();
  const run = useRun();
  const [editing, setEditing] = useState(false);
  const act = async (name: string, args: any, ok: string) => { if (await run(name, args, { ok })) onClose(); };
  const mine = item.employee_id && item.employee_id === me.user.employee_id;
  const canProgress = item.kind === "job" && (can.edit("schedule") || mine);

  if (editing) return <EventModal initial={item} onClose={onClose} employees={employees} bays={bays} />;
  return (
    <Drawer title={item.title} onClose={onClose}>
      <dl className="mb-4 grid grid-cols-[6rem_1fr] gap-y-1.5 text-sm">
        <dt className="text-slate-500">When</dt><dd>{item.all_day ? fmtDay(item.start) : fmtRange(item.start, item.end)}</dd>
        {item.status && <><dt className="text-slate-500">Status</dt><dd><StatusBadge status={item.status} />{item.overridden && <span className="ml-2"><Badge tone="amber">rule overridden</Badge></span>}</dd></>}
        {item.customer_name && <><dt className="text-slate-500">Customer</dt><dd>{item.customer_name}</dd></>}
        {item.vehicle && <><dt className="text-slate-500">Vehicle</dt><dd>{item.vehicle}</dd></>}
        {item.employee_name && <><dt className="text-slate-500">Mechanic</dt><dd>{item.employee_name}{item.supervisor_name && ` (supervised by ${item.supervisor_name})`}</dd></>}
        {item.bay_name && <><dt className="text-slate-500">Bay</dt><dd>{item.bay_name}</dd></>}
        {item.location && <><dt className="text-slate-500">Where</dt><dd>{item.location}</dd></>}
        {item.notes && <><dt className="text-slate-500">Notes</dt><dd className="whitespace-pre-wrap">{item.notes}</dd></>}
      </dl>
      {item.problem && <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"><b>Needs reassignment:</b> {item.problem}</div>}

      <div className="mb-5 flex flex-wrap gap-2">
        {item.work_order_id && <Link to={`/work-orders/${item.work_order_id}`} className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">Open work order #{item.work_order_number}</Link>}
        {canProgress && item.status === "planned" && <Button variant="primary" onClick={() => act("jobs.progress", { id: item.ref_id, action: "start" }, "Job started.")}>Start job</Button>}
        {canProgress && item.status === "in_progress" && <><Button onClick={() => act("jobs.progress", { id: item.ref_id, action: "pause" }, "Job paused.")}>Pause</Button><Button variant="primary" onClick={() => act("jobs.progress", { id: item.ref_id, action: "finish" }, "Job finished.")}>Finish job</Button></>}
        {canProgress && item.status === "paused" && <Button variant="primary" onClick={() => act("jobs.progress", { id: item.ref_id, action: "resume" }, "Job resumed.")}>Resume</Button>}
        {item.kind === "event" && <>
          <Button onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="danger" onClick={() => act("events.delete", { id: item.ref_id }, "Event deleted.")}>Delete</Button>
          {item.series_id && <Button variant="danger" onClick={() => act("events.delete", { id: item.ref_id, whole_series: true }, "This and later events deleted.")}>Delete this and later</Button>}
        </>}
        {item.kind === "bay_closure" && can.view("bays") && <Button variant="danger" onClick={() => act("bays.reopen", { id: item.ref_id }, "Closure removed.")}>Remove closure</Button>}
        {item.kind === "time_off" && <Button variant="danger" onClick={() => act("time_off.delete", { id: item.ref_id }, "Time off removed.")}>Remove time off</Button>}
      </div>
      {item.kind === "job" && item.editable && <p className="mb-4 text-xs text-slate-500">Drag this job on the calendar to move it. The scheduler checks certifications, the bay and shifts before accepting the move.</p>}

      {["job", "event", "hold"].includes(item.kind) && (
        <>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">History</h3>
          <div className="-mx-4"><EntityHistory type={item.kind === "event" ? "calendar_events" : "job_assignments"} id={item.ref_id} /></div>
        </>
      )}
    </Drawer>
  );
}

function EventModal({ initial, onClose, employees, bays }: { initial: any; onClose: () => void; employees: any[]; bays: any[] }) {
  const run = useRun();
  const existing = !!initial.ref_id;
  const [f, set] = useForm({
    title: initial.title ?? "", kind: initial.event_kind ?? "event", start: toInput(initial.start), end: toInput(initial.end), all_day: !!initial.all_day,
    notes: initial.notes ?? "", location: initial.location ?? "", employee_id: initial.employee_id ?? "", bay_id: initial.bay_id ?? "", repeat: "", repeat_until: "",
  });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    const base = { title: f.title, kind: f.kind, start: fromInput(f.start), end: fromInput(f.end), all_day: f.all_day, notes: f.notes || null, location: f.location || null,
      employee_id: f.employee_id || null, bay_id: f.bay_id || null };
    const ok = existing
      ? await run("events.update", { id: initial.ref_id, ...base }, { ok: "Event saved." })
      : await run("events.create", { ...base, repeat: f.repeat || null, repeat_until: f.repeat ? f.repeat_until || null : null }, { ok: "Event added." });
    setBusy(false);
    if (ok) onClose();
  }
  return (
    <Modal title={existing ? "Edit event" : "New calendar event"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={!f.title.trim() || !f.start || !f.end} onClick={save}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Title"><Input autoFocus value={f.title} onChange={set("title")} placeholder="e.g. Tool vendor visit" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Starts"><Input type="datetime-local" step={900} value={f.start} onChange={set("start")} /></Field>
          <Field label="Ends"><Input type="datetime-local" step={900} value={f.end} onChange={set("end")} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Type"><Select value={f.kind} onChange={set("kind")} options={[["event", "Event"], ["meeting", "Meeting"], ["reminder", "Reminder"], ["vendor", "Vendor visit"], ["closure", "Shop closure"]]} /></Field>
          <Field label="Where"><Input value={f.location} onChange={set("location")} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="For (optional)"><Select value={f.employee_id} onChange={set("employee_id")} placeholder="Everyone" options={employees.filter((e) => e.active).map((e) => [e.id, e.name] as const)} /></Field>
          <Field label="Bay (optional)"><Select value={f.bay_id} onChange={set("bay_id")} placeholder="No bay" options={bays.map((b: any) => [b.id, b.name] as const)} /></Field>
        </div>
        {!existing && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Repeat"><Select value={f.repeat} onChange={set("repeat")} placeholder="Doesn't repeat" options={[["daily", "Every day"], ["weekly", "Every week"], ["monthly", "Every month"]]} /></Field>
            {f.repeat && <Field label="Until"><Input type="date" value={f.repeat_until} onChange={set("repeat_until")} /></Field>}
          </div>
        )}
        <Checkbox label="All day" checked={f.all_day} onChange={set("all_day")} />
        <Field label="Notes"><Textarea value={f.notes} onChange={set("notes")} /></Field>
      </div>
    </Modal>
  );
}
