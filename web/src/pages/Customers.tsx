import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCan, useQ, useRun } from "../store";
import { fmtDateTime, GVWR_CLASSES } from "../lib";
import { AccessBanner, EMPTY_VEHICLE, EntityHistory, VehicleFields, vehiclePayload } from "../parts/shared";
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, PageHeader, StatusBadge, Table, Tabs, Textarea, useForm } from "../ui";

export function CustomersPage() {
  const nav = useNavigate();
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => { const t = setTimeout(() => setSearch(text), 200); return () => clearTimeout(t); }, [text]);
  const list = useQ<any[]>("customers.list", { q: search });
  const [adding, setAdding] = useState(false);
  return (
    <div>
      <PageHeader title="Customers" subtitle="Customers and their vehicles." actions={<Button variant="primary" onClick={() => setAdding(true)}>+ Add customer</Button>} />
      <AccessBanner vertical="customers" />
      <Input className="mb-3 max-w-md" placeholder="Search by name, phone, email, plate or VIN…" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="card">
        {list.isLoading ? <Loading /> : (
          <Table head={["Name", "Phone", "Email", "Vehicles"]} empty={!list.data?.length && <Empty title={search ? "No customers match" : "No customers yet"}>Customers are added when you complete an intake, or you can add one here.</Empty>}>
            {(list.data ?? []).map((c) => (
              <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
                <td className="td font-medium text-slate-900">{c.name}</td><td className="td">{c.phone ?? "—"}</td><td className="td">{c.email ?? "—"}</td><td className="td">{c.vehicle_count}</td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      {adding && <CustomerModal onClose={() => setAdding(false)} onSaved={(c) => nav(`/customers/${c.id}`)} />}
    </div>
  );
}

function CustomerModal({ customer, onClose, onSaved }: { customer?: any; onClose: () => void; onSaved?: (c: any) => void }) {
  const run = useRun();
  const [f, set] = useForm({ name: customer?.name ?? "", phone: customer?.phone ?? "", email: customer?.email ?? "", address: customer?.address ?? "", notes: customer?.notes ?? "" });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    const args = { name: f.name, phone: f.phone || null, email: f.email || null, address: f.address || null, notes: f.notes || null };
    const r: any = customer ? await run("customers.update", { id: customer.id, ...args }, { ok: "Customer saved." }) : await run("customers.create", args, { ok: "Customer added." });
    setBusy(false);
    if (r) { onClose(); if (r.id) onSaved?.(r); } else if (r === null) onClose();
  }
  return (
    <Modal title={customer ? "Edit customer" : "Add customer"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={!f.name.trim()} onClick={save}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Name"><Input autoFocus value={f.name} onChange={set("name")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Phone"><Input type="tel" value={f.phone} onChange={set("phone")} /></Field>
          <Field label="Email"><Input type="email" value={f.email} onChange={set("email")} /></Field>
        </div>
        <Field label="Address"><Input value={f.address} onChange={set("address")} /></Field>
        <Field label="Notes"><Textarea value={f.notes} onChange={set("notes")} /></Field>
      </div>
    </Modal>
  );
}

export function VehicleModal({ customerId, vehicle, onClose }: { customerId: string; vehicle?: any; onClose: () => void }) {
  const run = useRun();
  const [v, setV] = useState<any>(vehicle ? Object.fromEntries(Object.keys(EMPTY_VEHICLE).map((k) => [k, vehicle[k] == null ? "" : String(vehicle[k])])) : EMPTY_VEHICLE);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    const r = vehicle ? await run("vehicles.update", { id: vehicle.id, ...vehiclePayload(v) }, { ok: "Vehicle saved." })
      : await run("vehicles.create", { customer_id: customerId, ...vehiclePayload(v) }, { ok: "Vehicle added." });
    setBusy(false);
    if (r !== undefined) onClose();
  }
  return (
    <Modal wide title={vehicle ? "Edit vehicle" : "Add vehicle"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={!(v.make || v.model || v.vin || v.plate)} onClick={save}>Save</Button></>}>
      <VehicleFields value={v} onChange={setV} />
    </Modal>
  );
}

export function CustomerDetail() {
  const { id = "" } = useParams();
  const can = useCan();
  const nav = useNavigate();
  const c = useQ<any>("customers.get", { id });
  const calls = useQ<any[]>("calls.list", { customer_id: id }, { enabled: can.view("communications") });
  const [tab, setTab] = useState("overview");
  const [editing, setEditing] = useState(false);
  const [vehicle, setVehicle] = useState<any | "new" | null>(null);
  if (c.isLoading) return <Loading />;
  if (c.isError) return <Empty title="Customer not found" action={<Link className="link" to="/customers">Back to customers</Link>} />;
  const d = c.data;
  const tabs: [string, string][] = [["overview", "Overview"], ...(can.view("communications") ? [["calls", `Calls${d.call_count ? ` (${d.call_count})` : ""}`] as [string, string]] : []), ["history", "History"]];
  return (
    <div>
      <PageHeader title={d.name} subtitle={[d.phone, d.email].filter(Boolean).join(" · ") || "No contact details"}
        actions={<><Button onClick={() => setEditing(true)}>Edit</Button>{can.view("intake") && <Button variant="primary" onClick={() => nav("/intake/new")}>New intake</Button>}</>} />
      <AccessBanner vertical="customers" />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {tab === "overview" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Vehicles" pad={false} actions={<Button size="sm" onClick={() => setVehicle("new")}>+ Add vehicle</Button>}>
            {!d.vehicles.length ? <Empty title="No vehicles yet" /> : (
              <ul className="divide-y divide-slate-100">
                {d.vehicles.map((v: any) => (
                  <li key={v.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <div>
                      <div className="font-medium text-slate-900">{v.title}</div>
                      <div className="text-xs text-slate-500">{[v.plate, v.vin, v.engine, v.mileage && `${v.mileage.toLocaleString()} mi`].filter(Boolean).join(" · ")}</div>
                      <div className="mt-1 flex gap-1"><Badge>{v.powertrain}</Badge><Badge title={GVWR_CLASSES.find((g) => g[0] === v.gvwr_class)?.[1]}>class {v.gvwr_class}</Badge></div>
                    </div>
                    <Button size="sm" variant="ghost" onClick={() => setVehicle(v)}>Edit</Button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Work orders" pad={false}>
            {!d.work_orders.length ? <Empty title="No work orders yet" /> : (
              <ul className="divide-y divide-slate-100">
                {d.work_orders.map((w: any) => (
                  <li key={w.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                    <Link className="link font-medium" to={`/work-orders/${w.id}`}>#{w.number} {w.vehicle_title}</Link>
                    <span className="flex items-center gap-2"><span className="max-w-48 truncate text-slate-500">{w.concern}</span><StatusBadge status={w.status} /></span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {d.notes && <Card title="Notes"><p className="whitespace-pre-wrap text-sm">{d.notes}</p></Card>}
        </div>
      )}
      {tab === "calls" && (
        <Card pad={false}>
          {!calls.data?.length ? <Empty title="No calls on record">Transcripts of phone calls with this customer appear here.</Empty> : (
            <ul className="divide-y divide-slate-100">
              {calls.data.map((call) => (
                <li key={call.id} className="px-4 py-2.5 text-sm"><Link className="link font-medium" to={`/calls?call=${call.id}`}>{fmtDateTime(call.started_at)}</Link> <span className="text-slate-500">· {call.direction}</span><p className="text-slate-600">{call.summary ?? "No summary"}</p></li>
              ))}
            </ul>
          )}
        </Card>
      )}
      {tab === "history" && <Card pad={false}><EntityHistory type="customers" id={id} /></Card>}

      {editing && <CustomerModal customer={d} onClose={() => setEditing(false)} />}
      {vehicle && <VehicleModal customerId={id} vehicle={vehicle === "new" ? undefined : vehicle} onClose={() => setVehicle(null)} />}
    </div>
  );
}
