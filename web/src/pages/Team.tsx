import { useState } from "react";
import { useCan, useNow, useQ, useRun } from "../store";
import { addDays, cx, fmtDate, fmtDay, fmtTime, iso, isoDate, parse, sameDay, startOfWeek, WEEKDAYS } from "../lib";
import { AccessBanner } from "../parts/shared";
import { Badge, Button, Card, Checkbox, Empty, Field, Input, Loading, Modal, PageHeader, Select, StatusBadge, Tabs, useForm } from "../ui";

export function TeamPage() {
  const can = useCan();
  const [tab, setTab] = useState("people");
  return (
    <div>
      <PageHeader title="Team" subtitle="Who works here, what they're certified for, and when they work." />
      <Tabs value={tab} onChange={setTab} tabs={[["people", "People & certifications"], ...(can.view("shifts") ? [["shifts", "Shifts & time off"] as const] : [])]} />
      {tab === "people" ? <><AccessBanner vertical="team" /><People /></> : <><AccessBanner vertical="shifts" /><Shifts /></>}
    </div>
  );
}

const KINDS = [["tech", "Mechanic"], ["apprentice", "Apprentice"], ["advisor", "Service advisor"], ["owner", "Owner"]] as const;

function People() {
  const run = useRun();
  const employees = useQ<any[]>("employees.list");
  const [editing, setEditing] = useState<any | "new" | null>(null);
  const [certFor, setCertFor] = useState<any | null>(null);
  if (employees.isLoading) return <Loading />;
  return (
    <>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setEditing("new")}>+ Add employee</Button></div>
      <div className="grid gap-4 lg:grid-cols-2">
        {(employees.data ?? []).map((e) => (
          <Card key={e.id} className={e.active ? "" : "opacity-60"}
            title={<span className="flex items-center gap-2">{e.name} <Badge>{KINDS.find((k) => k[0] === e.kind)?.[1]}</Badge>{e.can_supervise && <Badge tone="teal">can supervise</Badge>}{!e.active && <Badge>inactive</Badge>}</span>}
            actions={<Button size="sm" variant="ghost" onClick={() => setEditing(e)}>Edit</Button>}>
            {["tech", "apprentice"].includes(e.kind) && <p className="mb-2 text-xs text-slate-500">Skill level {e.skill_level} of 5</p>}
            {e.certs.length === 0 ? <p className="text-sm text-slate-400">No certifications recorded.</p> : (
              <ul className="space-y-1">
                {e.certs.map((c: any) => (
                  <li key={c.id} className="flex items-center justify-between gap-2 text-sm">
                    <span>{c.name}{c.legally_required && <span className="ml-1 text-xs text-slate-400">(legal requirement)</span>}</span>
                    <span className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
                      {c.expires_on ? `expires ${fmtDate(c.expires_on)}` : "no expiry"} <StatusBadge status={c.status} />
                      <button className="text-slate-400 hover:text-red-600" aria-label={`Remove ${c.name}`} onClick={() => confirm(`Remove ${c.name} from ${e.name}? Jobs that need it will be flagged.`) && run("certs.remove", { id: c.id }, { ok: "Certification removed." })}>✕</button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {["tech", "apprentice"].includes(e.kind) && <Button size="sm" className="mt-3" onClick={() => setCertFor(e)}>+ Add certification</Button>}
          </Card>
        ))}
      </div>
      {!employees.data?.length && <div className="card"><Empty title="No employees yet" /></div>}
      {editing && <EmployeeModal employee={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {certFor && <CertModal employee={certFor} onClose={() => setCertFor(null)} />}
    </>
  );
}

function EmployeeModal({ employee, onClose }: { employee?: any; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ name: employee?.name ?? "", kind: employee?.kind ?? "tech", skill_level: String(employee?.skill_level ?? 3), can_supervise: !!employee?.can_supervise, active: employee?.active ?? true, phone: employee?.phone ?? "", email: employee?.email ?? "" });
  return (
    <Modal title={employee ? "Edit employee" : "Add employee"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.name.trim()} onClick={async () => {
      const args: any = { name: f.name, kind: f.kind, skill_level: Number(f.skill_level), can_supervise: f.can_supervise, phone: f.phone || null, email: f.email || null };
      const r = employee ? await run("employees.update", { id: employee.id, ...args, active: f.active }, { ok: "Saved." }) : await run("employees.create", args, { ok: "Employee added." });
      if (r !== undefined) onClose();
    }}>Save</Button></>}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name" className="col-span-2"><Input autoFocus value={f.name} onChange={set("name")} /></Field>
        <Field label="Role"><Select value={f.kind} onChange={set("kind")} options={KINDS} /></Field>
        <Field label="Skill level" hint="1 = entry, 5 = master"><Select value={f.skill_level} onChange={set("skill_level")} options={[1, 2, 3, 4, 5].map((n) => [String(n), String(n)] as const)} /></Field>
        <Field label="Phone"><Input value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Email"><Input value={f.email} onChange={set("email")} /></Field>
        <div className="col-span-2 space-y-2">
          <Checkbox label="Can supervise apprentices" checked={f.can_supervise} onChange={set("can_supervise")} />
          {employee && <div><Checkbox label="Active (can be scheduled)" checked={f.active} onChange={set("active")} /></div>}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">An apprentice is only scheduled when a supervisor who holds the job's certifications is on shift.</p>
    </Modal>
  );
}

function CertModal({ employee, onClose }: { employee: any; onClose: () => void }) {
  const run = useRun();
  const types = useQ<any[]>("cert_types.list");
  const [f, set] = useForm({ cert_type_id: "", number: "", issued_on: "", expires_on: "" });
  const type = types.data?.find((t) => t.id === f.cert_type_id);
  return (
    <Modal title={`Add certification for ${employee.name}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.cert_type_id || (type?.has_expiry && !f.expires_on)} onClick={async () => {
      if ((await run("certs.add", { employee_id: employee.id, cert_type_id: f.cert_type_id, number: f.number || null, issued_on: f.issued_on || null, expires_on: f.expires_on || null }, { ok: "Certification added." })) !== undefined) onClose();
    }}>Add</Button></>}>
      <div className="space-y-3">
        <Field label="Certification">
          <select className="input" value={f.cert_type_id} onChange={set("cert_type_id")} autoFocus>
            <option value="">Choose…</option>
            {(types.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}{t.legally_required ? " (legal requirement)" : ""}</option>)}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Issued"><Input type="date" value={f.issued_on} onChange={set("issued_on")} /></Field>
          <Field label="Expires" hint={type && !type.has_expiry ? "This one doesn't expire." : "A job is only assigned if this is still valid on the day of the job."}><Input type="date" value={f.expires_on} onChange={set("expires_on")} disabled={type && !type.has_expiry} /></Field>
        </div>
        <Field label="Certificate number"><Input value={f.number} onChange={set("number")} /></Field>
      </div>
    </Modal>
  );
}

function Shifts() {
  const now = useNow();
  const run = useRun();
  const [week, setWeek] = useState(startOfWeek(now));
  const employees = useQ<any[]>("employees.list");
  const data = useQ<any>("shifts.list", { start: iso(week), end: iso(addDays(week, 7)) });
  const [adding, setAdding] = useState<any | null>(null);
  const [off, setOff] = useState<any | null>(null);
  const staff = (employees.data ?? []).filter((e) => e.active && ["tech", "apprentice"].includes(e.kind));
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-md border border-slate-300">
          <button className="bg-white px-2.5 py-1.5 hover:bg-slate-50" onClick={() => setWeek(addDays(week, -7))} aria-label="Previous week">‹</button>
          <button className="border-x border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50" onClick={() => setWeek(startOfWeek(now))}>This week</button>
          <button className="bg-white px-2.5 py-1.5 hover:bg-slate-50" onClick={() => setWeek(addDays(week, 7))} aria-label="Next week">›</button>
        </div>
        <span className="mr-auto font-semibold text-slate-800">Week of {fmtDay(week, { month: "long", day: "numeric" })}</span>
        <Button onClick={() => setOff({})}>+ Time off</Button>
        <Button variant="primary" onClick={() => setAdding({})}>+ Add shift</Button>
      </div>
      <div className="card overflow-x-auto">
        {data.isLoading ? <Loading /> : !staff.length ? <Empty title="No mechanics yet">Add your mechanics under People first.</Empty> : (
          <table className="min-w-full border-collapse text-sm">
            <thead><tr className="bg-slate-50"><th className="th w-40">Mechanic</th>{days.map((d, i) => <th key={i} className={cx("th", sameDay(d, now) && "text-brand-700")}>{WEEKDAYS[i]} {d.getDate()}</th>)}</tr></thead>
            <tbody>
              {staff.map((e) => (
                <tr key={e.id} className="border-t border-slate-100">
                  <td className="td font-medium">{e.name}</td>
                  {days.map((d, i) => {
                    const shifts = (data.data?.shifts ?? []).filter((s: any) => s.employee_id === e.id && sameDay(parse(s.starts_at), d));
                    const offs = (data.data?.time_off ?? []).filter((t: any) => t.employee_id === e.id && parse(t.starts_at) < addDays(d, 1) && parse(t.ends_at) > d);
                    return (
                      <td key={i} className="td min-w-28 align-top">
                        {shifts.map((s: any) => (
                          <div key={s.id} className="group mb-1 flex items-center justify-between rounded bg-lime-50 px-1.5 py-0.5 text-xs text-lime-900">
                            <span>{fmtTime(s.starts_at)}–{fmtTime(s.ends_at)}</span>
                            <button className="ml-1 text-lime-700 opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100" aria-label="Delete shift"
                              onClick={() => run("shifts.delete", { id: s.id, whole_series: !!s.series_id && confirm("Also remove the later shifts in this repeating series?") }, { ok: "Shift removed." })}>✕</button>
                          </div>
                        ))}
                        {offs.map((t: any) => (
                          <div key={t.id} className="group mb-1 flex items-center justify-between rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-700">
                            <span>Off{t.reason ? `: ${t.reason}` : ""}</span>
                            <button className="ml-1 opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100" aria-label="Remove time off" onClick={() => run("time_off.delete", { id: t.id }, { ok: "Time off removed." })}>✕</button>
                          </div>
                        ))}
                        {!shifts.length && !offs.length && <button className="w-full rounded border border-dashed border-slate-200 py-0.5 text-xs text-slate-300 hover:border-brand-500 hover:text-brand-700" onClick={() => setAdding({ employee_id: e.id, date: isoDate(d) })}>+</button>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {adding && <ShiftModal initial={adding} staff={staff} week={week} onClose={() => setAdding(null)} />}
      {off && <TimeOffModal staff={staff} week={week} onClose={() => setOff(null)} />}
    </>
  );
}

function ShiftModal({ initial, staff, week, onClose }: { initial: any; staff: any[]; week: Date; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ employee_id: initial.employee_id ?? staff[0]?.id ?? "", date: initial.date ?? isoDate(week), start: "08:00", end: "17:00", repeat: false, until: isoDate(addDays(week, 27)) });
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4]);
  return (
    <Modal title="Add shift" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.employee_id} onClick={async () => {
      const r: any = await run("shifts.create", { employee_id: f.employee_id, start: `${f.date}T${f.start}:00`, end: `${f.date}T${f.end}:00`,
        repeat_weekdays: f.repeat ? days : null, repeat_until: f.repeat ? f.until : null });
      if (r !== undefined) onClose();
    }}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Mechanic"><Select value={f.employee_id} onChange={set("employee_id")} options={staff.map((e) => [e.id, e.name] as const)} /></Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label={f.repeat ? "Starting" : "Date"}><Input type="date" value={f.date} onChange={set("date")} /></Field>
          <Field label="From"><Input type="time" step={900} value={f.start} onChange={set("start")} /></Field>
          <Field label="To"><Input type="time" step={900} value={f.end} onChange={set("end")} /></Field>
        </div>
        <Checkbox label="Repeat every week" checked={f.repeat} onChange={set("repeat")} />
        {f.repeat && (
          <>
            <div className="flex gap-1">{WEEKDAYS.map((d, i) => <button key={d} aria-pressed={days.includes(i)} onClick={() => setDays((x) => (x.includes(i) ? x.filter((y) => y !== i) : [...x, i]))} className={cx("rounded border px-2 py-1 text-xs font-medium", days.includes(i) ? "border-brand-600 bg-brand-50 text-brand-800" : "border-slate-300 text-slate-500")}>{d}</button>)}</div>
            <Field label="Until"><Input type="date" value={f.until} onChange={set("until")} /></Field>
          </>
        )}
      </div>
    </Modal>
  );
}

function TimeOffModal({ staff, week, onClose }: { staff: any[]; week: Date; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ employee_id: staff[0]?.id ?? "", from: isoDate(week), to: isoDate(week), reason: "" });
  return (
    <Modal title="Add time off" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.employee_id} onClick={async () => {
      const r: any = await run("time_off.create", { employee_id: f.employee_id, start: `${f.from}T00:00:00`, end: `${isoDate(addDays(parse(f.to + "T00:00:00"), 1))}T00:00:00`, reason: f.reason || null }, { ok: "Time off added. Any affected jobs are flagged for reassignment." });
      if (r !== undefined) onClose();
    }}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Mechanic"><Select value={f.employee_id} onChange={set("employee_id")} options={staff.map((e) => [e.id, e.name] as const)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="First day off"><Input type="date" value={f.from} onChange={set("from")} /></Field>
          <Field label="Last day off"><Input type="date" value={f.to} min={f.from} onChange={set("to")} /></Field>
        </div>
        <Field label="Reason"><Input value={f.reason} onChange={set("reason")} placeholder="e.g. Vacation" /></Field>
      </div>
    </Modal>
  );
}
