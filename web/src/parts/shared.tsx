// Feature components used by several screens.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, cmd, q } from "../api";
import { useCan, useMe, useNow, useQ, useRun } from "../store";
import { GVWR_CLASSES, POWERTRAINS, addDays, fmtDateTime, fmtMinutes, fmtRange, fmtTime, isoDate } from "../lib";
import { Badge, Button, Diff, Empty, Field, Input, Loading, Modal, Notice, Select, Textarea, useForm, useToast } from "../ui";

// ---- Permissions ------------------------------------------------------------------

/** Shown at the top of an area the user can only view: explains that edits are
 *  sent for approval, and offers to request edit rights for the whole area. */
export function AccessBanner({ vertical }: { vertical: string }) {
  const me = useMe();
  const can = useCan();
  const run = useRun();
  const [open, setOpen] = useState(false);
  const [f, set] = useForm({ reason: "", hours: "8" });
  const mine = useQ<any>("access.list", {}, { enabled: !can.edit(vertical) });
  if (can.edit(vertical) || !me.grantable.includes(vertical)) return null;
  const pending = mine.data?.requests.some((r: any) => r.vertical === vertical && r.status === "pending");
  return (
    <>
      <Notice tone="blue" className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <span>You have view access to <b>{me.verticals[vertical]}</b>. Changes you make here are sent to an owner or advisor for approval.</span>
        {pending ? <Badge tone="amber">Edit access requested</Badge> : <Button size="sm" onClick={() => setOpen(true)}>Request edit access</Button>}
      </Notice>
      {open && (
        <Modal title={`Request edit access: ${me.verticals[vertical]}`} onClose={() => setOpen(false)}
          footer={<><Button onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" onClick={async () => {
              if (await run("access.request", { vertical, reason: f.reason || null, hours: f.hours ? Number(f.hours) : null }, { ok: "Request sent." })) setOpen(false);
            }}>Send request</Button></>}>
          <div className="space-y-3">
            <Field label="Why do you need it?"><Textarea value={f.reason} onChange={set("reason")} placeholder="e.g. Covering the front desk this afternoon" /></Field>
            <Field label="For how long?">
              <Select value={f.hours} onChange={set("hours")} options={[["4", "4 hours"], ["8", "8 hours"], ["24", "1 day"], ["168", "1 week"], ["", "Until revoked"]]} />
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}

// ---- History ----------------------------------------------------------------------

export function HistoryList({ rows }: { rows: any[] }) {
  if (!rows.length) return <Empty title="No changes recorded yet" />;
  return (
    <ol className="divide-y divide-slate-100">
      {rows.map((r) => (
        <li key={r.id} className="px-4 py-2.5">
          <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="font-medium text-slate-900">{r.actor_name}</span>
            {r.actor_type === "agent" && <Badge tone="violet">via assistant</Badge>}
            {r.actor_type === "intake_link" && <Badge tone="teal">intake form</Badge>}
            {r.actor_type === "integration" && <Badge tone="blue">integration</Badge>}
            <span className="text-slate-600">{r.op === "insert" ? "created" : r.op === "delete" ? "deleted" : "changed"} {r.entity_label.toLowerCase()}</span>
            {r.label && <span className="font-medium text-slate-800">{r.label}</span>}
            <span className="ml-auto text-xs text-slate-400">{fmtDateTime(r.at)}</span>
          </div>
          {r.approved_by_name && <div className="text-xs text-slate-500">Approved by {r.approved_by_name}</div>}
          <div className="mt-1"><Diff op={r.op} before={r.before} after={r.after} changed={r.changed_fields} /></div>
        </li>
      ))}
    </ol>
  );
}

export function EntityHistory({ type, id }: { type: string; id: string }) {
  const rows = useQ<any[]>("history.list", { entity_type: type, entity_id: id, limit: 50 });
  if (rows.isLoading) return <Loading />;
  return <HistoryList rows={rows.data ?? []} />;
}

// ---- Vehicles ---------------------------------------------------------------------

export const EMPTY_VEHICLE = { vin: "", plate: "", year: "", make: "", model: "", trim: "", engine: "", gvwr_class: "1", weight_lb: "", length_ft: "", powertrain: "gas", mileage: "", color: "", notes: "" };

export function vehiclePayload(v: any) {
  const num = (x: any) => (x === "" || x == null ? null : Number(x));
  return { ...v, year: num(v.year), gvwr_class: Number(v.gvwr_class || 1), weight_lb: num(v.weight_lb), length_ft: num(v.length_ft), mileage: num(v.mileage),
    vin: v.vin || null, plate: v.plate || null, make: v.make || null, model: v.model || null, trim: v.trim || null, engine: v.engine || null, color: v.color || null, notes: v.notes || null };
}

/** Vehicle fields with VIN decoding. `decode` is injectable so the public intake form can use its own endpoint. */
export function VehicleFields({ value, onChange, decode, compact }: { value: any; onChange: (v: any) => void; decode?: (vin: string) => Promise<any>; compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "green" | "amber"; text: string } | null>(null);
  const set = (k: string) => (e: any) => onChange({ ...value, [k]: e.target.value });

  async function decodeVin() {
    setBusy(true);
    setNote(null);
    try {
      const d = await (decode ?? ((vin: string) => q("vin.decode", { vin })))(value.vin);
      onChange({
        ...value, vin: d.vin ?? value.vin, year: d.year ?? value.year, make: d.make ?? value.make, model: d.model ?? value.model,
        trim: d.trim ?? value.trim, engine: d.engine ?? value.engine, powertrain: d.powertrain ?? value.powertrain,
        gvwr_class: d.gvwr_class ? String(d.gvwr_class) : value.gvwr_class,
      });
      setNote({ tone: "green", text: `Decoded: ${[d.year, d.make, d.model].filter(Boolean).join(" ")}. Please check the details.` });
    } catch (e: any) {
      setNote({ tone: "amber", text: e?.message ?? "Couldn't decode that VIN. Enter the details by hand." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-end gap-2">
        <Field label="VIN" className="flex-1" hint="17 characters. Decoding fills in the rest.">
          <Input value={value.vin} onChange={(e) => onChange({ ...value, vin: e.target.value.toUpperCase() })} maxLength={20} placeholder="e.g. 1FTFW1E50NFA00000" />
        </Field>
        <Button onClick={decodeVin} busy={busy} disabled={(value.vin ?? "").replace(/\s/g, "").length < 11} className="mb-[22px]">Decode</Button>
      </div>
      {note && <Notice tone={note.tone}>{note.text}</Notice>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Year"><Input inputMode="numeric" value={value.year} onChange={set("year")} /></Field>
        <Field label="Make"><Input value={value.make} onChange={set("make")} /></Field>
        <Field label="Model"><Input value={value.model} onChange={set("model")} /></Field>
        <Field label="Licence plate"><Input value={value.plate} onChange={set("plate")} /></Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Fuel / powertrain" hint="Decides which certifications a job needs."><Select value={value.powertrain} onChange={set("powertrain")} options={POWERTRAINS} /></Field>
        <Field label="Weight class" hint="Decides which bays can take it."><Select value={value.gvwr_class} onChange={set("gvwr_class")} options={GVWR_CLASSES} /></Field>
      </div>
      {!compact && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Engine"><Input value={value.engine} onChange={set("engine")} /></Field>
          <Field label="Mileage"><Input inputMode="numeric" value={value.mileage} onChange={set("mileage")} /></Field>
          <Field label="Weight (lb)"><Input inputMode="numeric" value={value.weight_lb} onChange={set("weight_lb")} /></Field>
          <Field label="Length (ft)"><Input inputMode="decimal" value={value.length_ft} onChange={set("length_ft")} /></Field>
        </div>
      )}
    </div>
  );
}

// ---- Scheduling: rule overrides ---------------------------------------------------

/**
 * Runs a scheduling command. If the scheduler refuses it for a reason a manager
 * may override (never a legally required certification, never a double booking),
 * asks the manager for a reason and retries.
 */
export function useScheduleCommand(onDone?: (result: any) => void) {
  const qc = useQueryClient();
  const toast = useToast();
  const can = useCan();
  const [blocked, setBlocked] = useState<{ name: string; args: any; violations: any[]; canOverride: boolean; ok?: string } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(name: string, args: any, ok?: string): Promise<boolean> {
    setBusy(true);
    try {
      const res = await cmd(name, args);
      qc.invalidateQueries({ queryKey: ["q"] });
      if (res.status === "pending") toast("Sent to an owner or advisor for approval.", "info");
      else {
        if (ok) toast(ok);
        onDone?.(res.result);
      }
      setBlocked(null);
      setReason("");
      return true;
    } catch (e: any) {
      if (e instanceof ApiError && e.extra?.violations) setBlocked({ name, args, violations: e.extra.violations, canOverride: !!e.extra.can_override, ok });
      else toast(e?.message ?? "Something went wrong.", "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const modal = blocked && (
    <Modal title="This doesn't fit the scheduling rules" onClose={() => setBlocked(null)}
      footer={<>
        <Button onClick={() => setBlocked(null)}>Cancel</Button>
        {blocked.canOverride && can.manager && (
          <Button variant="danger" busy={busy} disabled={reason.trim().length < 3} onClick={() => run(blocked.name, { ...blocked.args, override_reason: reason }, blocked.ok)}>Override and book</Button>
        )}
      </>}>
      <ul className="mb-3 space-y-1.5">
        {blocked.violations.map((v, i) => (
          <li key={i} className="flex gap-2 text-sm"><span className={v.overridable ? "text-amber-600" : "text-red-600"}>{v.overridable ? "▲" : "■"}</span><span>{v.message}</span></li>
        ))}
      </ul>
      {!blocked.canOverride ? (
        <Notice tone="red">This can't be overridden. Choose a different mechanic, bay or time.</Notice>
      ) : can.manager ? (
        <Field label="Reason for overriding (kept in the history)"><Textarea autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Working under my direct supervision" /></Field>
      ) : (
        <Notice>Only an owner or advisor can override these rules.</Notice>
      )}
    </Modal>
  );
  return { run, modal, busy };
}

// ---- Scheduling: slot picker ------------------------------------------------------

export function SlotPicker({ workOrderId, onBooked }: { workOrderId: string; onBooked?: (r: any) => void }) {
  const me = useMe();
  const today = isoDate(useNow());
  const [f, set] = useForm({ from: "", days: "14" });
  const args = { work_order_id: workOrderId, days: Number(f.days), earliest: f.from ? `${f.from}T00:00:00` : undefined };
  const slots = useQ<any>("schedule.find_slots", args);
  const sched = useScheduleCommand(onBooked);
  const d = slots.data;

  const bookArgs = (c: any) => ({
    work_order_id: workOrderId,
    steps: c.steps.map((s: any) => ({ line_id: s.line_id, step_id: s.step_id, employee_id: s.employee_id, bay_id: s.bay_id, supervisor_id: s.supervisor_id, start: s.start, end: s.end })),
  });

  return (
    <div>
      {sched.modal}
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Field label="Not before"><Input type="date" min={today} value={f.from} onChange={set("from")} /></Field>
        <Field label="Look ahead"><Select value={f.days} onChange={set("days")} options={[["3", "3 days"], ["7", "1 week"], ["14", "2 weeks"], ["30", "30 days"]]} /></Field>
        {d && <p className="pb-2 text-xs text-slate-500">Times shown in {me.shop.timezone.replace(/_/g, " ")}.</p>}
      </div>

      {slots.isLoading && <Loading />}
      {slots.isError && <Notice tone="red">{(slots.error as any)?.message}</Notice>}

      {d && (
        <>
          <div className="mb-3 flex flex-wrap gap-2 text-xs">
            {d.steps.map((s: any, i: number) => (
              <span key={i} className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
                <b>{s.label}</b> · {fmtMinutes(s.minutes)}{!s.hands_on && " (waiting)"}
                {s.certs.map((g: string[], j: number) => <span key={j} className="ml-1 text-slate-500">· needs {g.join(" or ")}</span>)}
                {s.bay_capability && <span className="ml-1 text-slate-500">· {s.bay_capability.replace(/_/g, " ")} bay</span>}
              </span>
            ))}
          </div>
          <PartsStatus parts={d.parts} />

          {d.blockers.length > 0 && (
            <Notice tone="amber" className="mb-3">
              <p className="mb-1 font-medium">Nothing fits between {fmtDateTime(d.searched.from)} and {fmtDateTime(d.searched.to)}:</p>
              <ul className="list-disc pl-5">{d.blockers.map((b: any, i: number) => <li key={i}>{b.message}</li>)}</ul>
              {d.relaxations.length > 0 && (
                <ul className="mt-2 space-y-0.5 border-t border-amber-200 pt-2">{d.relaxations.map((r: any, i: number) => <li key={i}>→ {r.message}</li>)}</ul>
              )}
            </Notice>
          )}

          <ul className="space-y-2">
            {d.candidates.map((c: any, i: number) => (
              <li key={i} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3 hover:border-brand-500">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-900">{fmtRange(c.start, c.end)}</span>
                    {i === 0 && <Badge tone="teal">Best match</Badge>}
                    {c.reasons.map((r: string) => <Badge key={r}>{r}</Badge>)}
                  </div>
                  <div className="mt-1 space-y-0.5 text-sm text-slate-600">
                    {c.steps.map((s: any, j: number) => (
                      <div key={j}>
                        {c.steps.length > 1 && <span className="text-slate-400">{fmtTime(s.start)}–{fmtTime(s.end)} · </span>}
                        {s.hands_on ? <b className="font-medium text-slate-800">{s.employee_name}</b> : <span className="italic">no mechanic needed</span>}
                        {s.supervisor_name && <span> (supervised by {s.supervisor_name})</span>}
                        {s.bay_name && <span> · {s.bay_name}</span>}
                        {c.steps.length > 1 && <span className="text-slate-400"> · {s.label}</span>}
                      </div>
                    ))}
                    {c.holds.map((h: any, j: number) => <div key={"h" + j} className="text-xs text-slate-400">Vehicle stays in {h.bay_name} {fmtRange(h.start, h.end)}</div>)}
                  </div>
                </div>
                <Button variant="primary" busy={sched.busy} onClick={() => sched.run("schedule.book", bookArgs(c), "Booked.")}>Book</Button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function PartsStatus({ parts }: { parts: any }) {
  if (!parts?.lines?.length) return null;
  const tone: Record<string, string> = { in_stock: "green", on_order: "blue", order_needed: "amber", unavailable: "red" };
  const label: Record<string, string> = { in_stock: "In stock", on_order: "On order", order_needed: "Needs ordering", unavailable: "Not available" };
  return (
    <div className="mb-3 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
      <span className="mr-2 font-medium text-slate-700">Parts:</span>
      {parts.lines.map((p: any) => (
        <span key={p.part_id} className="mr-3 inline-flex items-center gap-1">
          {p.name} ×{p.needed} <Badge tone={tone[p.status]}>{label[p.status]}{p.eta ? `, due ${fmtDateTime(p.eta)}` : ""}</Badge>
        </span>
      ))}
    </div>
  );
}

// ---- Customer picker --------------------------------------------------------------

export function CustomerPicker({ value, onChange }: { value: any | null; onChange: (c: any | null) => void }) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => { const t = setTimeout(() => setDebounced(text), 200); return () => clearTimeout(t); }, [text]);
  const results = useQ<any[]>("customers.list", { q: debounced, limit: 8 }, { enabled: debounced.length >= 2 });
  if (value) {
    return (
      <div className="flex items-center justify-between rounded-md border border-brand-200 bg-brand-50 px-3 py-2 text-sm">
        <span><b>{value.name}</b>{value.phone && <span className="ml-2 text-slate-500">{value.phone}</span>}</span>
        <Button size="sm" variant="ghost" onClick={() => onChange(null)}>Change</Button>
      </div>
    );
  }
  return (
    <div>
      <Input placeholder="Search by name, phone, email, plate or VIN…" value={text} onChange={(e) => setText(e.target.value)} autoFocus />
      {debounced.length >= 2 && (
        <ul className="mt-1 divide-y divide-slate-100 rounded-md border border-slate-200 bg-white">
          {(results.data ?? []).map((c) => (
            <li key={c.id}>
              <button onClick={() => onChange(c)} className="flex w-full justify-between px-3 py-2 text-left text-sm hover:bg-slate-50">
                <span><b>{c.name}</b> <span className="text-slate-500">{c.phone}</span></span>
                <span className="text-xs text-slate-400">{c.vehicle_count} vehicle{c.vehicle_count === 1 ? "" : "s"}</span>
              </button>
            </li>
          ))}
          {results.data?.length === 0 && <li className="px-3 py-2 text-sm text-slate-500">No existing customer matches.</li>}
        </ul>
      )}
    </div>
  );
}

export const Section = ({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) => (
  <div className="mb-5">
    <div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>{right}</div>
    {children}
  </div>
);

export const tomorrow = (now: Date) => isoDate(addDays(now, 1));
