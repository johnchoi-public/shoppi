// Live bay board: what is in each bay right now, what is next, and how full the day is.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useCan, useMe, useNow, useQ, useRun } from "../store";
import { cx, fmtDateTime, fmtRange, fmtTime, fromInput, iso, isoDate, addDays } from "../lib";
import { AccessBanner } from "../parts/shared";
import { Badge, Button, Empty, Field, Input, Loading, Modal, PageHeader, Select, useForm } from "../ui";

const STATUS: Record<string, [string, string, string]> = {
  free: ["Free", "bg-emerald-500", "border-emerald-200"],
  after_hours: ["Closed for the day", "bg-slate-400", "border-slate-200"],
  occupied: ["In use", "bg-brand-600", "border-brand-200"],
  scheduled_now: ["Booked, not started", "bg-sky-500", "border-sky-200"],
  paused: ["Paused", "bg-amber-500", "border-amber-200"],
  holding: ["Holding vehicle", "bg-slate-500", "border-slate-300"],
  closed: ["Closed", "bg-rose-500", "border-rose-200"],
};

export function BaysPage() {
  const me = useMe();
  const can = useCan();
  const now = useNow();
  const run = useRun();
  const [day, setDay] = useState(isoDate(now));
  const board = useQ<any[]>("bays.board", { day }, { refetchInterval: 30_000 });
  const [closing, setClosing] = useState<any | null>(null);
  const progress = (id: string, action: string, ok: string) => run("jobs.progress", { id, action }, { ok });

  return (
    <div>
      <PageHeader title="Bays" subtitle="Live status of every service bay. Each bay keeps its own hours and schedule."
        actions={<>
          <Input type="date" value={day} onChange={(e) => setDay(e.target.value || isoDate(now))} className="w-40" aria-label="Day for utilisation" />
          <Link to={`/calendar?view=day&by=bay&date=${day}`} className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">Bay timelines</Link>
          {can.manager && <Link to="/settings/bays" className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">Manage bays</Link>}
        </>} />
      <AccessBanner vertical="bays" />

      {board.isLoading ? <Loading /> : !board.data?.length ? (
        <div className="card"><Empty title="No bays yet" action={can.manager && <Link className="link" to="/settings/bays">Add your first bay</Link>}>Add your service bays with their lift capacity and equipment so jobs can be scheduled into the right one.</Empty></div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {board.data.map((b) => {
            const [label, dot, border] = STATUS[b.status] ?? STATUS.free;
            const cur = b.current;
            const mine = cur?.employee_id && cur.employee_id === me.user.employee_id;
            const canAct = cur && !cur.is_hold && (can.edit("schedule") || mine);
            return (
              <section key={b.bay.id} className={cx("card flex flex-col border-2", border)}>
                <header className="flex items-start justify-between gap-2 px-4 pt-3">
                  <div>
                    <h2 className="text-base font-semibold text-slate-900">{b.bay.name}</h2>
                    <p className="text-xs text-slate-500">Up to class {b.bay.max_gvwr_class}{b.bay.max_weight_lb ? ` · ${b.bay.max_weight_lb.toLocaleString()} lb` : ""}</p>
                  </div>
                  <span className="flex items-center gap-1.5 text-sm font-medium text-slate-700"><span className={cx("h-2.5 w-2.5 rounded-full", dot)} />{label}</span>
                </header>
                <div className="flex flex-wrap gap-1 px-4 pt-2">{b.bay.capabilities.map((c: string) => <Badge key={c}>{c.replace(/_/g, " ")}</Badge>)}</div>

                <div className="flex-1 px-4 py-3">
                  {b.closure ? (
                    <div className="rounded-md bg-rose-50 p-3 text-sm text-rose-900">
                      <b>{b.closure.reason || "Closed"}</b><div className="text-xs">until {fmtDateTime(b.closure.ends_at)}</div>
                      {can.edit("bays") && <Button size="sm" className="mt-2" onClick={() => run("bays.reopen", { id: b.closure.id }, { ok: "Bay reopened." })}>Reopen now</Button>}
                    </div>
                  ) : cur ? (
                    <div className="rounded-md bg-slate-50 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <Link to={`/work-orders/${cur.work_order_id}`} className="font-medium text-slate-900 hover:underline">{cur.vehicle || "Vehicle"}{cur.plate && <span className="ml-1 text-xs text-slate-500">{cur.plate}</span>}</Link>
                        <span className="text-xs text-slate-500">WO #{cur.work_order_number}</span>
                      </div>
                      <div className="text-sm text-slate-600">{cur.is_hold ? "Waiting between steps" : cur.label}</div>
                      <div className="mt-1 text-xs text-slate-500">{cur.employee_name ?? "No mechanic"} · {cur.customer} · due {fmtTime(cur.end)}</div>
                      {b.overrunning && <div className="mt-1 text-xs font-medium text-red-700">Running past its scheduled finish{b.next[0] ? `: next job starts ${fmtTime(b.next[0].start)}` : ""}</div>}
                      {canAct && (
                        <div className="mt-2 flex gap-2">
                          {cur.status === "planned" && <Button size="sm" variant="primary" onClick={() => progress(cur.id, "start", "Job started.")}>Start</Button>}
                          {cur.status === "in_progress" && <><Button size="sm" onClick={() => progress(cur.id, "pause", "Job paused.")}>Pause</Button><Button size="sm" variant="primary" onClick={() => progress(cur.id, "finish", "Job finished.")}>Finish</Button></>}
                          {cur.status === "paused" && <Button size="sm" variant="primary" onClick={() => progress(cur.id, "resume", "Job resumed.")}>Resume</Button>}
                        </div>
                      )}
                    </div>
                  ) : <p className="py-3 text-sm text-slate-400">No vehicle in this bay.</p>}

                  {b.next.length > 0 && (
                    <div className="mt-3">
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Up next</div>
                      <ul className="space-y-1 text-sm">
                        {b.next.map((n: any) => (
                          <li key={n.id} className="flex justify-between gap-2"><Link className="truncate hover:underline" to={`/work-orders/${n.work_order_id}`}>{n.is_hold ? "Hold: " : ""}{n.vehicle} <span className="text-slate-400">· {n.employee_name ?? "—"}</span></Link><span className="shrink-0 text-xs text-slate-500">{fmtDateTime(n.start)}</span></li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                <footer className="border-t border-slate-100 px-4 py-2.5">
                  <div className="mb-1 flex justify-between text-xs text-slate-500">
                    <span>{b.open_today.length ? `Open ${b.open_today.map((o: any) => `${fmtTime(o.start)}–${fmtTime(o.end)}`).join(", ")}` : "Closed this day"}</span>
                    <span>{b.utilisation.percent}% booked</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className={cx("h-full rounded-full", b.utilisation.percent > 90 ? "bg-red-500" : b.utilisation.percent > 60 ? "bg-amber-500" : "bg-brand-500")} style={{ width: `${Math.min(100, b.utilisation.percent)}%` }} /></div>
                  {!b.closure && <Button size="sm" variant="ghost" className="mt-2 -ml-2" onClick={() => setClosing(b.bay)}>Close bay for a period…</Button>}
                </footer>
              </section>
            );
          })}
        </div>
      )}
      {closing && <CloseBayModal bay={closing} onClose={() => setClosing(null)} />}
    </div>
  );
}

export function CloseBayModal({ bay, onClose }: { bay: any; onClose: () => void }) {
  const run = useRun();
  const now = useNow();
  const [f, set] = useForm({ start: iso(now).slice(0, 16), end: iso(addDays(now, 1)).slice(0, 11) + "08:00", reason: "" });
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={`Close ${bay.name}`} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} onClick={async () => {
        setBusy(true);
        const r: any = await run("bays.close", { bay_id: bay.id, start: fromInput(f.start), end: fromInput(f.end), reason: f.reason || null });
        setBusy(false);
        if (r) onClose();
      }}>Close bay</Button></>}>
      <div className="space-y-3">
        <p className="text-sm text-slate-600">Jobs already booked in this bay during the closure will be flagged for reassignment.</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From"><Input type="datetime-local" value={f.start} onChange={set("start")} /></Field>
          <Field label="Until"><Input type="datetime-local" value={f.end} onChange={set("end")} /></Field>
        </div>
        <Field label="Reason"><Select value={f.reason} onChange={set("reason")} placeholder="Choose…" options={[["Lift maintenance", "Lift maintenance"], ["Equipment failure", "Equipment failure"], ["Reserved", "Reserved"], ["Cleaning", "Cleaning"], ["Other", "Other"]]} /></Field>
      </div>
    </Modal>
  );
}
