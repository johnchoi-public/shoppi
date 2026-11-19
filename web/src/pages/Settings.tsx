import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { get } from "../api";
import { useCan, useMe, useQ, useRun } from "../store";
import { cx, fmtDateTime, fmtMinutes, fmtMoney, GVWR_CLASSES, POWERTRAINS, WEEKDAYS } from "../lib";
import { Badge, Button, Card, Checkbox, Empty, Field, Input, Loading, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, Textarea, useForm, useToast } from "../ui";
import { CloseBayModal } from "./Bays";

export function SettingsPage() {
  const { tab = "shop" } = useParams();
  const nav = useNavigate();
  const can = useCan();
  const tabs: [string, string][] = [["shop", "Shop"], ["services", "Services"], ["bays", "Bays"], ["members", "Members"], ...(can.manager ? [["integrations", "Integrations"] as [string, string]] : [])];
  return (
    <div>
      <PageHeader title="Settings" />
      <Tabs tabs={tabs} value={tab} onChange={(t) => nav(`/settings/${t}`)} />
      {tab === "shop" && <ShopSettings />}
      {tab === "services" && <Services />}
      {tab === "bays" && <Bays />}
      {tab === "members" && <Members />}
      {tab === "integrations" && can.manager && <Integrations />}
    </div>
  );
}

// ---- Shop -------------------------------------------------------------------------

function ShopSettings() {
  const can = useCan();
  const run = useRun();
  const shop = useQ<any>("shop.get");
  if (shop.isLoading) return <Loading />;
  return <ShopForm shop={shop.data} canEdit={can.manager} run={run} />;
}

function ShopForm({ shop, canEdit, run }: { shop: any; canEdit: boolean; run: any }) {
  const [f, set] = useForm({ name: shop.name, phone: shop.phone ?? "", address: shop.address ?? "", timezone: shop.timezone });
  const [hours, setHours] = useState<Record<string, string[] | null>>(shop.hours ?? {});
  const s = shop.settings ?? {};
  const [opts, setOpt] = useForm({ mechanics_can_read_calls: !!s.mechanics_can_read_calls, call_retention_days: s.call_retention_days ? String(s.call_retention_days) : "", supervision_cap: String(s.supervision_cap ?? 2) });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Shop details">
        <div className="space-y-3">
          <Field label="Shop name"><Input value={f.name} onChange={set("name")} disabled={!canEdit} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Phone"><Input value={f.phone} onChange={set("phone")} disabled={!canEdit} /></Field>
            <Field label="Timezone" hint="IANA name, e.g. America/Chicago"><Input value={f.timezone} onChange={set("timezone")} disabled={!canEdit} /></Field>
          </div>
          <Field label="Address"><Input value={f.address} onChange={set("address")} disabled={!canEdit} /></Field>
        </div>
      </Card>
      <Card title="Opening hours">
        <p className="mb-3 text-xs text-slate-500">Bays use these hours unless a bay has its own (Settings → Bays).</p>
        <HoursEditor value={hours} onChange={setHours} disabled={!canEdit} />
      </Card>
      <Card title="Scheduling & privacy" className="lg:col-span-2">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Apprentices per supervisor" hint="How many apprentice jobs one supervisor can oversee at once."><Select value={opts.supervision_cap} onChange={setOpt("supervision_cap")} disabled={!canEdit} options={[["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"]]} /></Field>
          <Field label="Keep call transcripts for" hint="Older calls are deleted automatically."><Select value={opts.call_retention_days} onChange={setOpt("call_retention_days")} disabled={!canEdit} placeholder="Forever" options={[["30", "30 days"], ["90", "90 days"], ["365", "1 year"], ["730", "2 years"]]} /></Field>
          <div className="pt-5"><Checkbox label="Mechanics may read call transcripts" checked={opts.mechanics_can_read_calls} onChange={setOpt("mechanics_can_read_calls")} disabled={!canEdit} /></div>
        </div>
      </Card>
      {canEdit && (
        <div className="lg:col-span-2">
          <Button variant="primary" onClick={() => run("shop.update", {
            name: f.name, phone: f.phone || null, address: f.address || null, timezone: f.timezone, hours,
            settings: { mechanics_can_read_calls: opts.mechanics_can_read_calls, call_retention_days: opts.call_retention_days ? Number(opts.call_retention_days) : null, supervision_cap: Number(opts.supervision_cap) },
          }, { ok: "Settings saved." })}>Save settings</Button>
        </div>
      )}
    </div>
  );
}

function HoursEditor({ value, onChange, disabled }: { value: Record<string, string[] | null>; onChange: (v: Record<string, string[] | null>) => void; disabled?: boolean }) {
  return (
    <div className="space-y-1.5">
      {WEEKDAYS.map((day, i) => {
        const pair = value[String(i)];
        return (
          <div key={day} className="flex items-center gap-2 text-sm">
            <span className="w-10 font-medium text-slate-600">{day}</span>
            <input type="checkbox" className="accent-teal-700" checked={!!pair} disabled={disabled} aria-label={`${day} open`} onChange={(e) => onChange({ ...value, [i]: e.target.checked ? ["08:00", "17:00"] : null })} />
            {pair ? (
              <>
                <input type="time" step={900} className="input w-28" value={pair[0]} disabled={disabled} onChange={(e) => onChange({ ...value, [i]: [e.target.value, pair[1]] })} />
                <span className="text-slate-400">to</span>
                <input type="time" step={900} className="input w-28" value={pair[1]} disabled={disabled} onChange={(e) => onChange({ ...value, [i]: [pair[0], e.target.value] })} />
              </>
            ) : <span className="text-slate-400">Closed</span>}
          </div>
        );
      })}
    </div>
  );
}

// ---- Services ---------------------------------------------------------------------

function Services() {
  const can = useCan();
  const run = useRun();
  const services = useQ<any[]>("services.list");
  const [editing, setEditing] = useState<any | "new" | null>(null);
  if (services.isLoading) return <Loading />;
  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-sm text-slate-500">Each service lists its steps, how long they take, and the certifications a mechanic needs. The scheduler uses this to decide who can do a job, and in which bay.</p>
        {can.edit("services") && <Button variant="primary" onClick={() => setEditing("new")}>+ Add service</Button>}
      </div>
      <div className="card">
        <Table head={["Service", "Time", "Price", "Needs", ""]} empty={!services.data?.length && <Empty title="No services yet" />}>
          {(services.data ?? []).map((s) => (
            <tr key={s.id} className={s.active ? "" : "opacity-50"}>
              <td className="td"><span className="font-medium text-slate-900">{s.name}</span>{s.is_diagnosis && <span className="ml-2"><Badge tone="teal">intake diagnosis</Badge></span>}{s.steps.length > 1 && <div className="text-xs text-slate-500">{s.steps.map((x: any) => x.name).join(" → ")}</div>}</td>
              <td className="td whitespace-nowrap">{fmtMinutes(s.minutes)}</td><td className="td">{fmtMoney(s.price)}</td>
              <td className="td">
                <div className="flex flex-wrap gap-1">
                  {Array.from(new Set(s.steps.flatMap((x: any) => x.requirements.map((r: any) => r.code)))).map((c: any) => <Badge key={c} tone="blue">{c}</Badge>)}
                  {Array.from(new Set(s.steps.map((x: any) => x.bay_capability).filter(Boolean))).map((c: any) => <Badge key={c}>{c.replace(/_/g, " ")} bay</Badge>)}
                  {s.parts.map((p: any) => <Badge key={p.part_id} tone="amber">{p.qty}× {p.name}</Badge>)}
                </div>
              </td>
              <td className="td whitespace-nowrap text-right">{can.edit("services") && <><Button size="sm" variant="ghost" onClick={() => setEditing(s)}>Edit</Button><Button size="sm" variant="ghost" onClick={() => run("services.archive", { id: s.id, archived: s.active }, { ok: s.active ? "Service hidden." : "Service restored." })}>{s.active ? "Hide" : "Restore"}</Button></>}</td>
            </tr>
          ))}
        </Table>
      </div>
      {editing && <ServiceModal service={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

const newStep = () => ({ name: "", minutes: "60", hands_on: true, needs_bay: true, bay_capability: "", holds_bay: true, same_mechanic_as_prev: false, requirements: [] as any[] });

function ServiceModal({ service, onClose }: { service?: any; onClose: () => void }) {
  const run = useRun();
  const certs = useQ<any[]>("cert_types.list");
  const bays = useQ<any>("bays.list");
  const parts = useQ<any[]>("parts.list");
  const [f, set] = useForm({ name: service?.name ?? "", price: service?.price != null ? String(service.price) : "", description: service?.description ?? "", is_diagnosis: !!service?.is_diagnosis });
  const [steps, setSteps] = useState<any[]>(service ? service.steps.map((s: any) => ({ ...s, minutes: String(s.minutes), bay_capability: s.bay_capability ?? "", requirements: s.requirements.map((r: any) => ({ ...r, applies_powertrain: r.applies_powertrain ?? "", applies_min_class: r.applies_min_class ? String(r.applies_min_class) : "" })) })) : [{ ...newStep(), name: "" }]);
  const [needs, setNeeds] = useState<any[]>(service?.parts.map((p: any) => ({ part_id: p.part_id, qty: String(p.qty) })) ?? []);
  const patch = (i: number, change: any) => setSteps((all) => all.map((s, j) => (j === i ? { ...s, ...change } : s)));
  const patchReq = (i: number, k: number, change: any) => patch(i, { requirements: steps[i].requirements.map((r: any, j: number) => (j === k ? { ...r, ...change } : r)) });

  async function save() {
    const payload = {
      id: service?.id, name: f.name, description: f.description || null, price: f.price ? Number(f.price) : null, active: service?.active ?? true,
      is_diagnosis: f.is_diagnosis,
      steps: steps.map((s) => ({
        name: s.name || f.name, minutes: Number(s.minutes || 60), hands_on: s.hands_on, needs_bay: s.needs_bay, bay_capability: s.bay_capability || null,
        holds_bay: s.holds_bay, same_mechanic_as_prev: s.same_mechanic_as_prev,
        requirements: s.requirements.filter((r: any) => r.cert_type_id).map((r: any) => ({ cert_type_id: r.cert_type_id, group_no: Number(r.group_no || 1), applies_powertrain: r.applies_powertrain || null, applies_min_class: r.applies_min_class ? Number(r.applies_min_class) : null })),
      })),
      parts: needs.filter((n) => n.part_id).map((n) => ({ part_id: n.part_id, qty: Number(n.qty || 1) })),
    };
    if ((await run("services.save", payload, { ok: "Service saved." })) !== undefined) onClose();
  }

  return (
    <Modal wide title={service ? `Edit ${service.name}` : "Add service"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.name.trim()} onClick={save}>Save service</Button></>}>
      <div className="mb-4 grid grid-cols-3 gap-3">
        <Field label="Name" className="col-span-2"><Input autoFocus value={f.name} onChange={set("name")} /></Field>
        <Field label="Price"><Input inputMode="decimal" value={f.price} onChange={set("price")} /></Field>
        <div className="col-span-3"><Checkbox label="Offer this as the “Diagnosis” option in customer intake (one service per shop)" checked={f.is_diagnosis} onChange={set("is_diagnosis")} /></div>
      </div>

      {steps.map((s, i) => (
        <div key={i} className="mb-3 rounded-lg border border-slate-200 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step {i + 1}</span>
            {steps.length > 1 && <Button size="sm" variant="ghost" onClick={() => setSteps(steps.filter((_, j) => j !== i))}>Remove step</Button>}
          </div>
          <div className="grid grid-cols-4 gap-3">
            <Field label="Step name" className="col-span-2"><Input value={s.name} onChange={(e) => patch(i, { name: e.target.value })} placeholder={f.name || "e.g. Teardown"} /></Field>
            <Field label="Minutes"><Input inputMode="numeric" value={s.minutes} onChange={(e) => patch(i, { minutes: e.target.value })} /></Field>
            <Field label="Bay must have"><Select value={s.bay_capability} onChange={(e) => patch(i, { bay_capability: e.target.value })} placeholder="Any bay" options={Object.entries(bays.data?.capabilities ?? {}) as any} /></Field>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
            <Checkbox label="A mechanic works on it" checked={s.hands_on} onChange={(e) => patch(i, { hands_on: (e.target as HTMLInputElement).checked })} />
            <Checkbox label="Needs a bay" checked={s.needs_bay} onChange={(e) => patch(i, { needs_bay: (e.target as HTMLInputElement).checked })} />
            <Checkbox label="Vehicle stays in the bay until the next step" checked={s.holds_bay} onChange={(e) => patch(i, { holds_bay: (e.target as HTMLInputElement).checked })} />
            {i > 0 && <Checkbox label="Same mechanic as the previous step" checked={s.same_mechanic_as_prev} onChange={(e) => patch(i, { same_mechanic_as_prev: (e.target as HTMLInputElement).checked })} />}
          </div>
          {s.hands_on && (
            <div className="mt-3">
              <span className="label">Certifications required</span>
              {s.requirements.map((r: any, k: number) => (
                <div key={k} className="mb-1.5 grid grid-cols-[1fr_5rem_8rem_8rem_auto] items-center gap-2">
                  <select className="input" value={r.cert_type_id} onChange={(e) => patchReq(i, k, { cert_type_id: e.target.value })}>
                    <option value="">Choose certification…</option>
                    {(certs.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}{c.legally_required ? " (legal)" : ""}</option>)}
                  </select>
                  <Select aria-label="Group" value={String(r.group_no ?? 1)} onChange={(e) => patchReq(i, k, { group_no: e.target.value })} options={[["1", "Set A"], ["2", "Set B"], ["3", "Set C"]]} />
                  <Select aria-label="Only for fuel type" value={r.applies_powertrain} onChange={(e) => patchReq(i, k, { applies_powertrain: e.target.value })} placeholder="Any fuel" options={POWERTRAINS} />
                  <Select aria-label="Only for weight class and up" value={r.applies_min_class} onChange={(e) => patchReq(i, k, { applies_min_class: e.target.value })} placeholder="Any size" options={GVWR_CLASSES.slice(2).map(([n]) => [String(n), `Class ${n}+`] as const)} />
                  <Button size="sm" variant="ghost" aria-label="Remove requirement" onClick={() => patch(i, { requirements: s.requirements.filter((_: any, j: number) => j !== k) })}>✕</Button>
                </div>
              ))}
              <Button size="sm" onClick={() => patch(i, { requirements: [...s.requirements, { cert_type_id: "", group_no: 1, applies_powertrain: "", applies_min_class: "" }] })}>+ Add requirement</Button>
              {s.requirements.length > 1 && <p className="mt-1 text-xs text-slate-500">Within a set, any one certification is enough. The mechanic needs one from every set.</p>}
            </div>
          )}
        </div>
      ))}
      <Button size="sm" onClick={() => setSteps([...steps, newStep()])}>+ Add another step</Button>

      <div className="mt-4">
        <span className="label">Parts this service uses</span>
        {needs.map((n, i) => (
          <div key={i} className="mb-1.5 grid grid-cols-[1fr_5rem_auto] gap-2">
            <Select value={n.part_id} onChange={(e) => setNeeds(needs.map((x, j) => (j === i ? { ...x, part_id: e.target.value } : x)))} placeholder="Choose part…" options={(parts.data ?? []).map((p) => [p.id, `${p.name} (${p.sku})`] as const)} />
            <Input inputMode="numeric" aria-label="Quantity" value={n.qty} onChange={(e) => setNeeds(needs.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))} />
            <Button size="sm" variant="ghost" aria-label="Remove part" onClick={() => setNeeds(needs.filter((_, j) => j !== i))}>✕</Button>
          </div>
        ))}
        <Button size="sm" onClick={() => setNeeds([...needs, { part_id: "", qty: "1" }])}>+ Add part</Button>
        <p className="mt-1 text-xs text-slate-500">A job is only offered once its parts are in stock or due in.</p>
      </div>
    </Modal>
  );
}

// ---- Bays -------------------------------------------------------------------------

function Bays() {
  const can = useCan();
  const run = useRun();
  const data = useQ<any>("bays.list");
  const [editing, setEditing] = useState<any | "new" | null>(null);
  const [closing, setClosing] = useState<any | null>(null);
  if (data.isLoading) return <Loading />;
  const bays = data.data.bays as any[];
  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-sm text-slate-500">Each bay has its own size limits, equipment and opening hours. Jobs are only scheduled into a bay that fits the vehicle and has what the work needs.</p>
        {can.edit("bays") && <Button variant="primary" onClick={() => setEditing("new")}>+ Add bay</Button>}
      </div>
      {!bays.length ? <div className="card"><Empty title="No bays yet" /></div> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {bays.map((b) => (
            <Card key={b.id} className={b.active ? "" : "opacity-60"} title={<span className="flex items-center gap-2">{b.name}{!b.active && <Badge>inactive</Badge>}</span>}
              actions={can.edit("bays") && <><Button size="sm" variant="ghost" onClick={() => setClosing(b)}>Close for a period</Button><Button size="sm" variant="ghost" onClick={() => setEditing(b)}>Edit</Button></>}>
              <p className="text-sm text-slate-600">Vehicles up to class {b.max_gvwr_class}{b.max_weight_lb ? `, ${b.max_weight_lb.toLocaleString()} lb` : ""}{b.max_length_ft ? `, ${b.max_length_ft} ft long` : ""}</p>
              <div className="mt-2 flex flex-wrap gap-1">{b.capabilities.map((c: string) => <Badge key={c}>{data.data.capabilities[c] ?? c}</Badge>)}</div>
              <p className="mt-2 text-xs text-slate-500">{b.hours.length ? "Own hours: " + b.hours.map((h: any) => `${WEEKDAYS[h.weekday]} ${h.closed ? "closed" : `${h.opens.slice(0, 5)}–${h.closes.slice(0, 5)}`}`).join(", ") : "Follows the shop's opening hours"}</p>
              {b.closures.map((c: any) => (
                <div key={c.id} className="mt-2 flex items-center justify-between rounded bg-rose-50 px-2 py-1 text-xs text-rose-900">
                  <span>Closed {fmtDateTime(c.starts_at)} – {fmtDateTime(c.ends_at)}{c.reason && ` · ${c.reason}`}</span>
                  {can.edit("bays") && <button className="hover:underline" onClick={() => run("bays.reopen", { id: c.id }, { ok: "Closure removed." })}>Remove</button>}
                </div>
              ))}
              {b.equipment.length > 0 && <p className="mt-2 text-xs text-slate-500">Equipment: {b.equipment.map((e: any) => e.name + (e.next_service_due ? ` (service due ${e.next_service_due})` : "")).join(", ")}</p>}
            </Card>
          ))}
        </div>
      )}
      {editing && <BayModal bay={editing === "new" ? undefined : editing} capabilities={data.data.capabilities} shopHours={data.data.shop_hours} order={bays.length} onClose={() => setEditing(null)} />}
      {closing && <CloseBayModal bay={closing} onClose={() => setClosing(null)} />}
    </>
  );
}

function BayModal({ bay, capabilities, shopHours, order, onClose }: { bay?: any; capabilities: Record<string, string>; shopHours: any; order: number; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ name: bay?.name ?? "", max_gvwr_class: String(bay?.max_gvwr_class ?? 3), max_weight_lb: bay?.max_weight_lb ? String(bay.max_weight_lb) : "", max_length_ft: bay?.max_length_ft ? String(bay.max_length_ft) : "", active: bay?.active ?? true, notes: bay?.notes ?? "" });
  const [caps, setCaps] = useState<string[]>(bay?.capabilities ?? ["lift"]);
  const [own, setOwn] = useState<boolean>(!!bay?.hours.length);
  const [hours, setHours] = useState<Record<string, string[] | null>>(() => {
    if (!bay?.hours.length) return shopHours ?? {};
    const out: Record<string, string[] | null> = {};
    for (let i = 0; i < 7; i++) out[i] = null;
    for (const h of bay.hours) out[h.weekday] = h.closed ? null : [h.opens.slice(0, 5), h.closes.slice(0, 5)];
    return out;
  });
  const [equipment, setEquipment] = useState("");

  async function save() {
    const r: any = await run("bays.save", {
      id: bay?.id, name: f.name, kind: caps.includes("heavy_lift") ? "heavy_duty" : caps.includes("alignment") ? "alignment" : "general", capabilities: caps,
      max_gvwr_class: Number(f.max_gvwr_class), max_weight_lb: f.max_weight_lb ? Number(f.max_weight_lb) : null, max_length_ft: f.max_length_ft ? Number(f.max_length_ft) : null,
      active: f.active, sort_order: bay?.sort_order ?? order, notes: f.notes || null,
      // Listing all seven days makes the bay's hours fully its own; an empty list returns it to shop hours.
      hours: own ? Array.from({ length: 7 }, (_, i) => (hours[i] ? { weekday: i, opens: hours[i]![0], closes: hours[i]![1] } : { weekday: i, closed: true })) : [],
    }, { ok: "Bay saved." });
    if (r && equipment.trim()) await run("bays.add_equipment", { bay_id: r.id, name: equipment.trim() }, { quiet: true });
    if (r !== undefined) onClose();
  }

  return (
    <Modal wide title={bay ? `Edit ${bay.name}` : "Add bay"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.name.trim()} onClick={save}>Save bay</Button></>}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Name" className="col-span-2"><Input autoFocus value={f.name} onChange={set("name")} placeholder="e.g. Bay 3" /></Field>
        <Field label="Largest vehicle" className="col-span-2"><Select value={f.max_gvwr_class} onChange={set("max_gvwr_class")} options={GVWR_CLASSES} /></Field>
        <Field label="Lift capacity (lb)" className="col-span-2"><Input inputMode="numeric" value={f.max_weight_lb} onChange={set("max_weight_lb")} /></Field>
        <Field label="Max length (ft)" className="col-span-2"><Input inputMode="decimal" value={f.max_length_ft} onChange={set("max_length_ft")} /></Field>
      </div>
      <div className="mt-4">
        <span className="label">Equipment in this bay</span>
        <div className="flex flex-wrap gap-2">
          {Object.entries(capabilities).map(([key, label]) => (
            <button key={key} aria-pressed={caps.includes(key)} onClick={() => setCaps((c) => (c.includes(key) ? c.filter((x) => x !== key) : [...c, key]))}
              className={cx("rounded-md border px-2.5 py-1 text-sm", caps.includes(key) ? "border-brand-600 bg-brand-50 font-medium text-brand-800" : "border-slate-300 text-slate-600")}>{label}</button>
          ))}
        </div>
      </div>
      <div className="mt-4">
        <Checkbox label="This bay has its own opening hours" checked={own} onChange={(e) => setOwn((e.target as HTMLInputElement).checked)} />
        {own && <div className="mt-2"><HoursEditor value={hours} onChange={setHours} /></div>}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Field label="Add a piece of equipment to track" hint="e.g. Two-post lift, serial 1234"><Input value={equipment} onChange={(e) => setEquipment(e.target.value)} /></Field>
        <Field label="Notes"><Input value={f.notes} onChange={set("notes")} /></Field>
      </div>
      {bay && <div className="mt-3"><Checkbox label="Active (can be scheduled)" checked={f.active} onChange={set("active")} /></div>}
    </Modal>
  );
}

// ---- Members ----------------------------------------------------------------------

function Members() {
  const me = useMe();
  const can = useCan();
  const run = useRun();
  const toast = useToast();
  const data = useQ<any>("members.list");
  const employees = useQ<any[]>("employees.list");
  const [inviting, setInviting] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  if (data.isLoading) return <Loading />;
  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-sm text-slate-500">Owners and advisors can change everything. Mechanics can view, and their changes go to an owner or advisor for approval unless they've been given edit access to an area.</p>
        {can.manager && <Button variant="primary" onClick={() => setInviting(true)}>+ Invite someone</Button>}
      </div>
      <div className="card">
        <Table head={["Name", "Email", "Role", "Status", ""]}>
          {data.data.members.map((m: any) => (
            <tr key={m.id}>
              <td className="td font-medium">{m.name}{m.user_id === me.user.id && <span className="ml-1 text-xs text-slate-400">(you)</span>}</td>
              <td className="td">{m.email}</td>
              <td className="td">
                {can.manager && m.user_id !== me.user.id
                  ? <Select className="w-32" value={m.role} onChange={(e) => run("members.update", { membership_id: m.id, role: e.target.value }, { ok: "Role changed." })} options={[["owner", "Owner"], ["advisor", "Advisor"], ["mechanic", "Mechanic"]]} />
                  : <span className="capitalize">{m.role}</span>}
              </td>
              <td className="td"><StatusBadge status={m.status} /></td>
              <td className="td text-right">{can.manager && m.user_id !== me.user.id && <Button size="sm" variant="ghost" onClick={() => run("members.update", { membership_id: m.id, status: m.status === "active" ? "disabled" : "active" }, { ok: m.status === "active" ? "Login disabled." : "Login restored." })}>{m.status === "active" ? "Disable login" : "Restore login"}</Button>}</td>
            </tr>
          ))}
        </Table>
      </div>
      {data.data.invites.length > 0 && (
        <Card title="Pending invites" pad={false} className="mt-4">
          <Table head={["Email", "Role", "Expires", ""]}>
            {data.data.invites.map((i: any) => <tr key={i.id}><td className="td">{i.email}</td><td className="td capitalize">{i.role}</td><td className="td">{fmtDateTime(i.expires_at)}</td><td className="td text-right"><Button size="sm" variant="ghost" onClick={() => run("members.revoke_invite", { id: i.id }, { ok: "Invite cancelled." })}>Cancel</Button></td></tr>)}
          </Table>
        </Card>
      )}
      {inviting && <InviteModal employees={employees.data ?? []} members={data.data.members} onClose={() => setInviting(false)} onLink={setLink} />}
      {link && (
        <Modal title="Invite link" onClose={() => setLink(null)} footer={<Button variant="primary" onClick={() => setLink(null)}>Done</Button>}>
          <p className="mb-2 text-sm text-slate-600">Send this link to them. It works once and expires in 7 days.</p>
          <div className="flex gap-2"><Input readOnly value={link} onFocus={(e) => e.target.select()} /><Button onClick={() => navigator.clipboard?.writeText(link).then(() => toast("Link copied."))}>Copy</Button></div>
          <Notice className="mt-3">Copy it now: for security it is only shown once. Shoppi doesn't send email itself.</Notice>
        </Modal>
      )}
    </>
  );
}

function InviteModal({ employees, members, onClose, onLink }: { employees: any[]; members: any[]; onClose: () => void; onLink: (l: string) => void }) {
  const run = useRun();
  const [f, set] = useForm({ email: "", role: "mechanic", employee_id: "" });
  const linked = new Set(members.map((m) => m.employee_id));
  const free = employees.filter((e) => e.active && !linked.has(e.id));
  return (
    <Modal title="Invite someone to the shop" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.email.includes("@")} onClick={async () => {
      const r: any = await run("members.invite", { email: f.email, role: f.role, employee_id: f.employee_id || null });
      if (r) { onClose(); onLink(r.link); }
    }}>Create invite</Button></>}>
      <div className="space-y-3">
        <Field label="Email"><Input type="email" autoFocus value={f.email} onChange={set("email")} /></Field>
        <Field label="Role"><Select value={f.role} onChange={set("role")} options={[["mechanic", "Mechanic (view, request changes)"], ["advisor", "Advisor (full access)"], ["owner", "Owner (full access)"]]} /></Field>
        <Field label="This person is" hint="Link the login to an existing employee so they see their own jobs."><Select value={f.employee_id} onChange={set("employee_id")} placeholder="Someone new (create an employee record)" options={free.map((e) => [e.id, e.name] as const)} /></Field>
      </div>
    </Modal>
  );
}

// ---- Integrations -----------------------------------------------------------------

const CATEGORY: Record<string, string> = { calendar: "Calendar", vehicle: "Vehicle data", parts: "Parts catalogs", telephony: "Phone calls" };

function Integrations() {
  const run = useRun();
  const toast = useToast();
  const [params] = useSearchParams();
  const list = useQ<any[]>("integrations.list");
  const [connecting, setConnecting] = useState<any | null>(null);
  const [secret, setSecret] = useState<any | null>(null);
  if (list.isLoading) return <Loading />;

  async function oauth(key: string) {
    try {
      const { url } = await get(`/api/integrations/${key}/connect`);
      location.assign(url);
    } catch (e: any) {
      toast(e?.message ?? "Couldn't start the connection.", "error");
    }
  }

  return (
    <>
      {params.get("error") && <Notice tone="red" className="mb-3">{params.get("error")}</Notice>}
      {params.get("connected") && <Notice tone="green" className="mb-3">Connected.</Notice>}
      <p className="mb-4 text-sm text-slate-500">Everything in Shoppi works without these. Integrations add outside data or mirror the schedule elsewhere.</p>
      {Object.entries(CATEGORY).map(([cat, label]) => {
        const items = (list.data ?? []).filter((i) => i.category === cat);
        if (!items.length) return null;
        return (
          <div key={cat} className="mb-5">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</h2>
            <div className="grid gap-3 lg:grid-cols-2">
              {items.map((i) => (
                <Card key={i.key} title={<span className="flex items-center gap-2">{i.name}{i.enabled ? <Badge tone="green">on</Badge> : <Badge>off</Badge>}</span>}
                  actions={<>
                    {i.connect === "none" && <Button size="sm" onClick={() => run("integrations.set_enabled", { key: i.key, enabled: !i.enabled }, { ok: i.enabled ? "Switched off." : "Switched on." })}>{i.enabled ? "Switch off" : "Switch on"}</Button>}
                    {i.connect === "oauth" && !i.connected && <Button size="sm" variant="primary" disabled={!i.available} onClick={() => oauth(i.key)}>Connect</Button>}
                    {i.connect === "api_key" && !i.connected && <Button size="sm" variant="primary" onClick={() => setConnecting(i)}>Connect</Button>}
                    {i.connect !== "none" && i.connected && <>
                      {i.category === "calendar" && <Button size="sm" onClick={() => run("integrations.sync_calendar", {}, { ok: "Sync started." })}>Sync now</Button>}
                      <Button size="sm" variant="danger" onClick={() => confirm(`Disconnect ${i.name}?`) && run("integrations.disconnect", { key: i.key }, { ok: "Disconnected." })}>Disconnect</Button>
                    </>}
                  </>}>
                  <p className="text-sm text-slate-600">{i.description}</p>
                  {!i.available && <Notice className="mt-2">{i.unavailable_reason}</Notice>}
                  {i.webhook_url && i.connected && (
                    <Field label="Send transcripts to this address" className="mt-3"><Input readOnly value={i.webhook_url} onFocus={(e) => e.target.select()} /></Field>
                  )}
                  {i.health.calls > 0 && (
                    <p className="mt-2 text-xs text-slate-500">
                      Last {i.health.calls} requests: <span className={i.health.failures ? "font-medium text-red-700" : "text-emerald-700"}>{i.health.failures} failed</span> · avg {i.health.avg_ms} ms · last {fmtDateTime(i.health.last_at)}{i.health.last_status !== "ok" && ` (${i.health.last_status})`}
                    </p>
                  )}
                </Card>
              ))}
            </div>
          </div>
        );
      })}
      {connecting && <ConnectModal integration={connecting} onClose={() => setConnecting(null)} onGenerated={setSecret} />}
      {secret && (
        <Modal title="Signing secret" onClose={() => setSecret(null)} footer={<Button variant="primary" onClick={() => setSecret(null)}>I've copied it</Button>}>
          <p className="mb-2 text-sm text-slate-600">Paste this into your phone system or transcription service. It signs each transcript so Shoppi knows it's genuine.</p>
          {Object.entries(secret).map(([k, v]) => <Field key={k} label={k}><Input readOnly value={String(v)} onFocus={(e) => e.target.select()} /></Field>)}
          <Notice className="mt-3">This is the only time it is shown. If you lose it, disconnect and connect again to make a new one.</Notice>
        </Modal>
      )}
    </>
  );
}

function ConnectModal({ integration, onClose, onGenerated }: { integration: any; onClose: () => void; onGenerated: (g: any) => void }) {
  const run = useRun();
  const [values, setValues] = useState<Record<string, string>>({});
  const missing = integration.fields.some((f: any) => f.required && !values[f.name]?.trim());
  return (
    <Modal title={`Connect ${integration.name}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={missing} onClick={async () => {
      const r: any = await run("integrations.connect", { key: integration.key, values }, { ok: "Connected." });
      if (r) { onClose(); if (r.generated && Object.keys(r.generated).length) onGenerated(r.generated); }
    }}>Connect</Button></>}>
      <div className="space-y-3">
        {integration.fields.map((f: any) => (
          <Field key={f.name} label={f.label + (f.required ? "" : " (optional)")} hint={f.help}>
            <Input type={f.secret ? "password" : "text"} autoComplete="off" value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
          </Field>
        ))}
        <p className="text-xs text-slate-500">Credentials are stored encrypted and are never shown again.</p>
      </div>
    </Modal>
  );
}

export const _keep = Textarea;
