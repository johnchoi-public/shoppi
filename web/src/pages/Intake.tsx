// Customer intake for staff: the review queue, and the guided intake wizard
// (its own full-screen flow, separate from the main app shell).
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { cmd } from "../api";
import { useCan, useQ, useRun } from "../store";
import { cx, fmtDateTime, fmtMinutes, fmtMoney } from "../lib";
import { AccessBanner, CustomerPicker, EMPTY_VEHICLE, SlotPicker, VehicleFields, vehiclePayload } from "../parts/shared";
import { Badge, Button, Card, Checkbox, Empty, Field, Input, Loading, Modal, Notice, PageHeader, Select, StatusBadge, Table, Textarea, useForm, useToast } from "../ui";

// ---- Review queue -----------------------------------------------------------------

export function IntakeQueue() {
  const can = useCan();
  const nav = useNavigate();
  const run = useRun();
  const queue = useQ<any[]>("intake.list", { status: "pending" });
  const links = useQ<any[]>("intake_links.list");
  const [newLink, setNewLink] = useState<any | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div>
      <PageHeader title="Intake" subtitle="New customers and vehicles: start an intake at the counter, or review what customers sent in themselves."
        />
      <AccessBanner vertical="intake" />

      <Card title={`Waiting for review${queue.data?.length ? ` (${queue.data.length})` : ""}`} pad={false} className="mb-5">
        {queue.isLoading ? <Loading /> : !queue.data?.length ? (
          <Empty title="Nothing waiting">Forms customers fill in from a kiosk or link, and drafts from phone calls, appear here for you to check before anything is created.</Empty>
        ) : (
          <ul className="divide-y divide-slate-100">
            {queue.data.map((s) => {
              const p = s.payload ?? {};
              const v = p.vehicle ?? {};
              return (
                <li key={s.id} className="flex flex-wrap items-start gap-4 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-slate-900">{p.customer?.name || "Unknown caller"}</span>
                      <Badge tone={s.source === "call" ? "blue" : "teal"}>{s.source === "call" ? "from a phone call" : "customer form"}</Badge>
                      <span className="text-xs text-slate-400">{fmtDateTime(s.created_at)}</span>
                    </div>
                    <div className="text-sm text-slate-600">{[p.customer?.phone, p.customer?.email].filter(Boolean).join(" · ")}</div>
                    <div className="text-sm text-slate-600">{[v.year, v.make, v.model].filter(Boolean).join(" ") || "Vehicle not given"}{v.plate && ` · ${v.plate}`}</div>
                    <p className="mt-1 text-sm text-slate-800">“{p.concern}”</p>
                    {p.preferred_times && <p className="text-xs text-slate-500">Prefers: {p.preferred_times}</p>}
                  </div>
                  <div className="flex gap-2">
                    <Button variant="primary" onClick={() => nav(`/intake/new?submission=${s.id}`)}>Review</Button>
                    <Button onClick={() => run("intake.reject", { id: s.id }, { ok: "Dismissed." })}>Dismiss</Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title="Customer intake links" pad={false}
        actions={can.edit("intake") && <><Button size="sm" busy={creating} onClick={async () => { setCreating(true); const r = await run("intake_links.create", { kind: "kiosk", label: "Front desk kiosk" }); setCreating(false); if (r) setNewLink(r); }}>+ Kiosk link</Button>
          <Button size="sm" onClick={async () => { const r = await run("intake_links.create", { kind: "single", label: "Customer link", expires_in_days: 14 }); if (r) setNewLink(r); }}>+ One-time link</Button></>}>
        {!links.data?.length ? <Empty title="No links yet">Create a kiosk link for a tablet at the counter, or a one-time link to text or email to a customer. They fill in their details; you review them here.</Empty> : (
          <Table head={["Link", "Type", "Used", "Created", "Status", ""]}>
            {links.data.map((l) => (
              <tr key={l.id}>
                <td className="td font-medium">{l.label}</td>
                <td className="td">{l.kind === "kiosk" ? "Reusable (kiosk)" : "One-time"}</td>
                <td className="td">{l.uses}</td>
                <td className="td text-slate-500">{fmtDateTime(l.created_at)}</td>
                <td className="td"><Badge tone={l.active ? "green" : "slate"}>{l.active ? "active" : "inactive"}</Badge></td>
                <td className="td text-right">{l.active && can.edit("intake") && <Button size="sm" variant="danger" onClick={() => run("intake_links.revoke", { id: l.id }, { ok: "Link revoked." })}>Revoke</Button>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {newLink && <LinkModal link={newLink} onClose={() => setNewLink(null)} />}
    </div>
  );
}

function LinkModal({ link, onClose }: { link: any; onClose: () => void }) {
  const toast = useToast();
  return (
    <Modal title="Intake link created" onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <p className="mb-2 text-sm text-slate-600">{link.kind === "kiosk" ? "Open this on the tablet at your counter. It can be used again and again." : "Send this to one customer. It works once and expires in 14 days."}</p>
      <div className="flex gap-2">
        <Input readOnly value={link.url} onFocus={(e) => e.target.select()} />
        <Button onClick={() => navigator.clipboard?.writeText(link.url).then(() => toast("Link copied."))}>Copy</Button>
      </div>
      <Notice className="mt-3">Copy it now: for security the full link is only shown once.</Notice>
    </Modal>
  );
}

// ---- Staff intake wizard ----------------------------------------------------------

const STEPS = ["Customer", "Vehicle", "Concern & services", "Schedule", "Done"];

export function IntakeWizard() {
  const nav = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const submissionId = params.get("submission");
  const submission = useQ<any>("intake.get", { id: submissionId }, { enabled: !!submissionId });
  const services = useQ<any[]>("services.list");
  const employees = useQ<any[]>("employees.list");

  const [step, setStep] = useState(0);
  const [customer, setCustomer] = useState<any | null>(null);
  const [newCustomer, setNewCustomer] = useForm({ name: "", phone: "", email: "", preferred_contact: "" });
  const [isNew, setIsNew] = useState(false);
  const [vehicleId, setVehicleId] = useState<string | null>(null);
  const [vehicle, setVehicle] = useState<any>(EMPTY_VEHICLE);
  const [f, set, setAll] = useForm({ concern: "", notes: "", mileage_in: "", preferred_employee_id: "" });
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any | null>(null);
  const [booked, setBooked] = useState<any | null>(null);
  const [pending, setPending] = useState(false);
  const existing = useQ<any>("customers.get", { id: customer?.id }, { enabled: !!customer?.id });

  // Prefill from a customer's own submission (or a phone-call draft).
  useEffect(() => {
    const p = submission.data?.payload;
    if (!p) return;
    setIsNew(true);
    setAll((v) => ({ ...v, concern: p.concern ?? "", notes: p.preferred_times ? `Customer prefers: ${p.preferred_times}` : "", mileage_in: p.vehicle?.mileage ? String(p.vehicle.mileage) : "" }));
    (setNewCustomer as any)("name")(p.customer?.name ?? "");
    (setNewCustomer as any)("phone")(p.customer?.phone ?? "");
    (setNewCustomer as any)("email")(p.customer?.email ?? "");
    const pv = p.vehicle ?? {};
    setVehicle({ ...EMPTY_VEHICLE, vin: pv.vin ?? "", plate: pv.plate ?? "", year: pv.year ? String(pv.year) : "", make: pv.make ?? "", model: pv.model ?? "",
      powertrain: pv.powertrain ?? "gas", gvwr_class: String(pv.gvwr_class ?? 1), mileage: pv.mileage ? String(pv.mileage) : "" });
  }, [submission.data?.id]);

  const matches = submission.data?.matches;
  useEffect(() => {
    const vehicles = existing.data?.vehicles ?? [];
    if (vehicleId || !vehicles.length) return;
    const same = (a?: string, b?: string) => !!a && !!b && a.trim().toLowerCase() === b.trim().toLowerCase();
    const hit = vehicles.find((x: any) => same(x.vin, vehicle.vin) || same(x.plate, vehicle.plate) || (same(x.make, vehicle.make) && same(x.model, vehicle.model)));
    if (hit) setVehicleId(hit.id);
  }, [existing.data?.id]);
  const customerReady = customer || (isNew && newCustomer.name.trim());
  const vehicleReady = vehicleId || vehicle.make || vehicle.model || vehicle.vin || vehicle.plate;
  const concernReady = f.concern.trim() || picked.length;

  async function createWorkOrder() {
    setBusy(true);
    setError("");
    const args: any = {
      concern: f.concern || null, notes: f.notes || null, service_type_ids: picked,
      mileage_in: f.mileage_in ? Number(f.mileage_in) : null, preferred_employee_id: f.preferred_employee_id || null,
    };
    if (customer) args.customer_id = customer.id;
    else args.customer = { name: newCustomer.name, phone: newCustomer.phone || null, email: newCustomer.email || null, preferred_contact: newCustomer.preferred_contact || null };
    if (vehicleId) args.vehicle_id = vehicleId;
    else args.vehicle = vehiclePayload(vehicle);
    try {
      const res = submissionId ? await cmd("intake.accept", { id: submissionId, ...args }) : await cmd("intake.staff_create", args);
      if (res.status === "pending") { setPending(true); setStep(4); }
      else { setResult(res.result); setStep(3); }
    } catch (e: any) {
      setError(e?.message ?? "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full flex-col bg-slate-100">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">New customer intake</h1>
            {submissionId && <p className="text-xs text-slate-500">Reviewing a {submission.data?.source === "call" ? "draft from a phone call" : "form the customer filled in"}</p>}
          </div>
          <Button variant="ghost" onClick={() => nav(result ? `/work-orders/${result.work_order.id}` : "/intake")}>{step >= 3 ? "Close" : "Cancel"}</Button>
        </div>
        <ol className="mx-auto flex max-w-3xl gap-1 px-4 pb-3" aria-label="Progress">
          {STEPS.map((s, i) => (
            <li key={s} className="flex-1">
              <div className={cx("h-1.5 rounded-full", i <= step ? "bg-brand-600" : "bg-slate-200")} />
              <div className={cx("mt-1 text-xs", i === step ? "font-semibold text-brand-800" : "text-slate-500")}>{i + 1}. {s}</div>
            </li>
          ))}
        </ol>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6">
        {error && <Notice tone="red" className="mb-4">{error}</Notice>}
        {submissionId && submission.isLoading && <Loading />}

        {step === 0 && (
          <Panel title="Who is the customer?">
            {matches?.customers?.length > 0 && !customer && (
              <Notice tone="blue" className="mb-3">
                <p className="mb-1 font-medium">This looks like an existing customer:</p>
                {matches.customers.map((c: any) => <button key={c.id} className="link mr-3" onClick={() => { setCustomer(c); setIsNew(false); }}>{c.name} ({c.phone})</button>)}
              </Notice>
            )}
            {!isNew ? (
              <>
                <CustomerPicker value={customer} onChange={(c) => { setCustomer(c); setVehicleId(null); }} />
                {!customer && <button className="link mt-3 text-sm" onClick={() => setIsNew(true)}>+ This is a new customer</button>}
              </>
            ) : (
              <div className="space-y-3">
                <Field label="Full name"><Input autoFocus value={newCustomer.name} onChange={setNewCustomer("name")} /></Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Phone"><Input type="tel" value={newCustomer.phone} onChange={setNewCustomer("phone")} /></Field>
                  <Field label="Email"><Input type="email" value={newCustomer.email} onChange={setNewCustomer("email")} /></Field>
                </div>
                <Field label="Best way to reach them"><Select value={newCustomer.preferred_contact} onChange={setNewCustomer("preferred_contact")} placeholder="No preference" options={[["phone", "Phone call"], ["text", "Text message"], ["email", "Email"]]} /></Field>
                <button className="link text-sm" onClick={() => setIsNew(false)}>Search existing customers instead</button>
              </div>
            )}
            <Nav next={() => setStep(1)} disabled={!customerReady} />
          </Panel>
        )}

        {step === 1 && (
          <Panel title="Which vehicle?">
            {customer && (existing.data?.vehicles?.length ?? 0) > 0 && (
              <div className="mb-4 space-y-2">
                {existing.data.vehicles.map((v: any) => (
                  <label key={v.id} className={cx("flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2", vehicleId === v.id ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-white")}>
                    <input type="radio" name="vehicle" checked={vehicleId === v.id} onChange={() => setVehicleId(v.id)} className="accent-teal-700" />
                    <span className="text-sm"><b>{v.title}</b> <span className="text-slate-500">{[v.plate, v.vin].filter(Boolean).join(" · ")}</span></span>
                  </label>
                ))}
                <label className={cx("flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2", !vehicleId ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-white")}>
                  <input type="radio" name="vehicle" checked={!vehicleId} onChange={() => setVehicleId(null)} className="accent-teal-700" /><span className="text-sm font-medium">A different vehicle</span>
                </label>
              </div>
            )}
            {!vehicleId && <VehicleFields value={vehicle} onChange={setVehicle} />}
            <Nav back={() => setStep(0)} next={() => setStep(2)} disabled={!vehicleReady} />
          </Panel>
        )}

        {step === 2 && (
          <Panel title="What do they need?">
            <div className="space-y-4">
              <Field label="Customer's concern" hint="In their words. If you don't pick a service, a diagnosis is added so the visit can still be scheduled.">
                <Textarea rows={3} autoFocus value={f.concern} onChange={set("concern")} placeholder="e.g. Grinding noise from the front when braking" />
              </Field>
              <div>
                <span className="label">Services</span>
                <div className="grid gap-2 sm:grid-cols-2">
                  {(services.data ?? []).filter((s) => s.active).map((s) => (
                    <label key={s.id} className={cx("flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2", picked.includes(s.id) ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-white")}>
                      <input type="checkbox" className="mt-0.5 accent-teal-700" checked={picked.includes(s.id)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, s.id] : p.filter((x) => x !== s.id)))} />
                      <span className="text-sm"><span className="font-medium">{s.name}</span><span className="block text-xs text-slate-500">{fmtMinutes(s.minutes)} · {fmtMoney(s.price)}</span></span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Mileage in"><Input inputMode="numeric" value={f.mileage_in} onChange={set("mileage_in")} /></Field>
                <Field label="Preferred mechanic"><Select value={f.preferred_employee_id} onChange={set("preferred_employee_id")} placeholder="No preference"
                  options={(employees.data ?? []).filter((e) => e.active && ["tech", "apprentice"].includes(e.kind)).map((e) => [e.id, e.name] as const)} /></Field>
              </div>
              <Field label="Notes for the shop"><Textarea rows={2} value={f.notes} onChange={set("notes")} /></Field>
            </div>
            <Nav back={() => setStep(1)} next={createWorkOrder} nextLabel="Create work order & find times" disabled={!concernReady} busy={busy} />
          </Panel>
        )}

        {step === 3 && result && (
          <Panel title={`Schedule work order #${result.work_order.number}`}>
            <p className="mb-4 text-sm text-slate-600">These times already account for who is certified for the work, which bays fit the vehicle, shifts and parts.</p>
            <SlotPicker workOrderId={result.work_order.id} onBooked={(r) => { setBooked(r); setStep(4); toast("Booked."); }} />
            <div className="mt-5 flex justify-end"><Button onClick={() => setStep(4)}>Skip scheduling for now</Button></div>
          </Panel>
        )}

        {step === 4 && (
          <Panel title={pending ? "Sent for approval" : "Intake complete"}>
            {pending ? (
              <Notice tone="blue">You have view access to intake, so this was sent to an owner or advisor to approve. You can follow it under Requests.</Notice>
            ) : (
              <div className="space-y-2 text-sm">
                <p>✓ Work order <b>#{result?.work_order.number}</b> for <b>{result?.work_order.customer_name}</b> · {result?.work_order.vehicle_title}</p>
                {booked ? <p>✓ Booked {fmtDateTime(booked.start)}{booked.jobs?.[0]?.mechanic && ` with ${booked.jobs[0].mechanic}`}{booked.jobs?.[0]?.bay && ` in ${booked.jobs[0].bay}`}</p>
                  : <p className="text-amber-700">Not scheduled yet. You can schedule it from the work order.</p>}
              </div>
            )}
            <div className="mt-6 flex flex-wrap gap-2">
              {result && <Button variant="primary" onClick={() => nav(`/work-orders/${result.work_order.id}`)}>Open work order</Button>}
              <Button onClick={() => location.assign("/intake/new")}>Start another intake</Button>
              <Link to="/intake" className="inline-flex items-center px-3 py-1.5 text-sm text-slate-600 hover:underline">Back to intake</Link>
            </div>
          </Panel>
        )}
      </main>
    </div>
  );
}

const Panel = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="card fade-in p-6"><h2 className="mb-4 text-lg font-semibold text-slate-900">{title}</h2>{children}</section>
);

function Nav({ back, next, disabled, nextLabel = "Continue", busy }: { back?: () => void; next: () => void; disabled?: boolean; nextLabel?: string; busy?: boolean }) {
  return (
    <div className="mt-6 flex justify-between">
      {back ? <Button onClick={back}>Back</Button> : <span />}
      <Button variant="primary" onClick={next} disabled={disabled} busy={busy}>{nextLabel}</Button>
    </div>
  );
}

export const _keep = [Checkbox, StatusBadge];
