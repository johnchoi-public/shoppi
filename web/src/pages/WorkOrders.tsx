import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCan, useQ, useRun } from "../store";
import { fmtDateTime, fmtMoney, fmtRange, titleCase } from "../lib";
import { AccessBanner, EntityHistory, PartsStatus, SlotPicker } from "../parts/shared";
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, Textarea, useForm } from "../ui";
import { FindParts } from "./Inventory";

export function WorkOrdersPage() {
  const nav = useNavigate();
  const [status, setStatus] = useState("open");
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => { const t = setTimeout(() => setSearch(text), 200); return () => clearTimeout(t); }, [text]);
  const list = useQ<any[]>("work_orders.list", { status: status === "all" ? undefined : status, q: search });
  return (
    <div>
      <PageHeader title="Work orders" subtitle="Every job, from intake to completion." />
      <AccessBanner vertical="work_orders" />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input className="max-w-xs" placeholder="Search number, customer, vehicle…" value={text} onChange={(e) => setText(e.target.value)} />
        <Select className="w-48" value={status} onChange={(e) => setStatus(e.target.value)}
          options={[["open", "Open"], ["draft", "Not scheduled"], ["scheduled", "Scheduled"], ["in_progress", "In progress"], ["waiting_parts", "Waiting for parts"], ["completed", "Completed"], ["cancelled", "Cancelled"], ["all", "All"]]} />
      </div>
      <div className="card">
        {list.isLoading ? <Loading /> : (
          <Table head={["#", "Customer", "Vehicle", "Concern", "Scheduled", "Status"]} empty={!list.data?.length && <Empty title="No work orders here">Start a customer intake to open one.</Empty>}>
            {(list.data ?? []).map((w) => (
              <tr key={w.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/work-orders/${w.id}`)}>
                <td className="td font-medium text-slate-900">#{w.number}</td>
                <td className="td">{w.customer_name}</td>
                <td className="td">{w.vehicle_title}{w.plate && <span className="ml-1 text-xs text-slate-400">{w.plate}</span>}</td>
                <td className="td max-w-64 truncate text-slate-600">{w.concern}</td>
                <td className="td whitespace-nowrap text-slate-600">{w.scheduled_start ? fmtDateTime(w.scheduled_start) : "—"}</td>
                <td className="td"><span className="flex gap-1"><StatusBadge status={w.status} />{w.needs_reassignment > 0 && <Badge tone="red">needs reassigning</Badge>}</span></td>
              </tr>
            ))}
          </Table>
        )}
      </div>
    </div>
  );
}

export function WorkOrderDetail() {
  const { id = "" } = useParams();
  const can = useCan();
  const run = useRun();
  const wo = useQ<any>("work_orders.get", { id });
  const services = useQ<any[]>("services.list");
  const [tab, setTab] = useState("overview");
  const [scheduling, setScheduling] = useState(false);
  const [addLine, setAddLine] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const nav = useNavigate();
  if (wo.isLoading) return <Loading />;
  if (wo.isError) return <Empty title="Work order not found" action={<Link className="link" to="/work-orders">Back to work orders</Link>} />;
  const d = wo.data;
  const open = !["completed", "cancelled"].includes(d.status);
  const jobs = d.jobs as any[];
  const started = jobs.some((j) => ["in_progress", "paused", "done"].includes(j.status));
  const flagged = jobs.filter((j) => j.status === "needs_reassignment");

  return (
    <div>
      <PageHeader
        title={<span className="flex items-center gap-3">Work order #{d.number} <StatusBadge status={d.status} /></span>}
        subtitle={<><Link className="link" to={`/customers/${d.customer_id}`}>{d.customer_name}</Link> · {d.vehicle_title}{d.plate && ` · ${d.plate}`}</>}
        actions={<>
          {open && <Button onClick={() => setEditing(true)}>Edit</Button>}
          {open && !started && <Button variant="primary" onClick={() => setScheduling(true)}>{jobs.length ? "Reschedule" : "Schedule"}</Button>}
          {open && jobs.length > 0 && !started && <Button onClick={() => run("schedule.unschedule", { id }, { ok: "Taken off the schedule." })}>Unschedule</Button>}
          {open && <Button onClick={() => run("work_orders.set_status", { id, status: "completed" }, { ok: "Work order completed." })}>Mark completed</Button>}
          {open && <Button variant="danger" onClick={() => confirm("Cancel this work order? Its schedule and reserved parts are released.") && run("work_orders.set_status", { id, status: "cancelled" }, { ok: "Work order cancelled." })}>Cancel</Button>}
          {!open && <Button onClick={() => run("work_orders.set_status", { id, status: "draft" }, { ok: "Reopened as a draft." })}>Reopen</Button>}
          {can.manager && <Button variant="danger" onClick={() => setDeleting(true)}>Delete</Button>}
        </>} />
      <AccessBanner vertical="work_orders" />

      {flagged.length > 0 && (
        <Notice tone="red" className="mb-4">
          <b>{flagged.length} scheduled job{flagged.length > 1 ? "s need" : " needs"} reassigning.</b>
          <ul className="mt-1 list-disc pl-5">{flagged.map((j) => <li key={j.id}>{j.label}: {j.problem}</li>)}</ul>
          <Button size="sm" className="mt-2" onClick={() => setScheduling(true)}>Find a new time</Button>
        </Notice>
      )}

      <Tabs tabs={[["overview", "Overview"], ...(can.view("inventory") ? [["parts", "Find parts"] as const] : []), ["history", "History"]]} value={tab} onChange={setTab} />

      {tab === "overview" && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <Card title="Concern"><p className="whitespace-pre-wrap text-sm">{d.concern || <span className="text-slate-400">None recorded</span>}</p>{d.notes && <p className="mt-2 whitespace-pre-wrap border-t border-slate-100 pt-2 text-sm text-slate-600">{d.notes}</p>}</Card>
            <Card title="Services" pad={false} actions={open && <Button size="sm" onClick={() => setAddLine(true)}>+ Add line</Button>}>
              <Table head={["Service", "Price", "Status", ""]} empty={!d.lines.length && <Empty title="No services yet" />}>
                {d.lines.map((l: any) => (
                  <tr key={l.id}>
                    <td className="td font-medium">{l.description}</td><td className="td">{fmtMoney(l.price)}</td><td className="td"><StatusBadge status={l.status} /></td>
                    <td className="td text-right">{open && l.status === "open" && <Button size="sm" variant="ghost" onClick={() => run("work_orders.remove_line", { id: l.id }, { ok: "Line removed." })}>Remove</Button>}</td>
                  </tr>
                ))}
                {d.lines.length > 0 && <tr><td className="td text-right font-medium">Total</td><td className="td font-semibold">{fmtMoney(d.total)}</td><td colSpan={2} /></tr>}
              </Table>
            </Card>
            <Card title="Schedule" pad={false}>
              {!jobs.length ? <Empty title="Not on the schedule yet" action={open && <Button variant="primary" onClick={() => setScheduling(true)}>Find a time</Button>} /> : (
                <Table head={["Step", "When", "Mechanic", "Bay", "Status", ""]}>
                  {jobs.map((j) => (
                    <tr key={j.id} className={j.is_hold ? "bg-slate-50 text-slate-500" : ""}>
                      <td className="td font-medium">{j.label}{j.override_reason && <div className="text-xs font-normal text-amber-700">Rule overridden: {j.override_reason}</div>}</td>
                      <td className="td whitespace-nowrap">{fmtRange(j.starts_at, j.ends_at)}</td>
                      <td className="td">{j.employee_name ?? "—"}{j.supervisor_name && <div className="text-xs text-slate-500">supervised by {j.supervisor_name}</div>}</td>
                      <td className="td">{j.bay_name ?? "—"}</td>
                      <td className="td"><StatusBadge status={j.status} /></td>
                      <td className="td text-right">
                        {!j.is_hold && j.status === "planned" && <Button size="sm" onClick={() => run("jobs.progress", { id: j.id, action: "start" }, { ok: "Job started." })}>Start</Button>}
                        {j.status === "in_progress" && <Button size="sm" variant="primary" onClick={() => run("jobs.progress", { id: j.id, action: "finish" }, { ok: "Job finished." })}>Finish</Button>}
                        {j.status === "paused" && <Button size="sm" onClick={() => run("jobs.progress", { id: j.id, action: "resume" }, { ok: "Job resumed." })}>Resume</Button>}
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
          <div className="space-y-4">
            <Card title="Vehicle">
              {d.vehicle && (
                <dl className="grid grid-cols-[6rem_1fr] gap-y-1 text-sm">
                  <dt className="text-slate-500">Vehicle</dt><dd>{d.vehicle.title}</dd>
                  <dt className="text-slate-500">VIN</dt><dd className="break-all">{d.vehicle.vin ?? "—"}</dd>
                  <dt className="text-slate-500">Plate</dt><dd>{d.vehicle.plate ?? "—"}</dd>
                  <dt className="text-slate-500">Fuel</dt><dd>{titleCase(d.vehicle.powertrain)}</dd>
                  <dt className="text-slate-500">Class</dt><dd>{d.vehicle.gvwr_class}</dd>
                  <dt className="text-slate-500">Mileage in</dt><dd>{d.mileage_in?.toLocaleString() ?? "—"}</dd>
                </dl>
              )}
            </Card>
            <Card title="Customer">
              <dl className="grid grid-cols-[6rem_1fr] gap-y-1 text-sm">
                <dt className="text-slate-500">Name</dt><dd><Link className="link" to={`/customers/${d.customer_id}`}>{d.customer?.name}</Link></dd>
                <dt className="text-slate-500">Phone</dt><dd>{d.customer?.phone ?? "—"}</dd>
                <dt className="text-slate-500">Email</dt><dd className="break-all">{d.customer?.email ?? "—"}</dd>
                <dt className="text-slate-500">Promised</dt><dd>{fmtDateTime(d.promised_at)}</dd>
              </dl>
            </Card>
            {d.parts.lines.length > 0 && <Card title="Parts needed"><PartsStatus parts={d.parts} /></Card>}
          </div>
        </div>
      )}
      {tab === "parts" && <FindParts workOrderId={id} />}
      {tab === "history" && <Card pad={false}><EntityHistory type="work_orders" id={id} /></Card>}

      {scheduling && <Modal wide title={`Schedule work order #${d.number}`} onClose={() => setScheduling(false)}><SlotPicker workOrderId={id} onBooked={() => setScheduling(false)} /></Modal>}
      {addLine && <AddLineModal workOrderId={id} services={services.data ?? []} onClose={() => setAddLine(false)} />}
      {editing && <EditModal wo={d} onClose={() => setEditing(false)} />}
      {deleting && <DeleteWorkOrderModal wo={d} onClose={() => setDeleting(false)} onDeleted={() => nav("/work-orders")} />}
    </div>
  );
}

function DeleteWorkOrderModal({ wo, onClose, onDeleted }: { wo: any; onClose: () => void; onDeleted: () => void }) {
  const run = useRun();
  const [busy, setBusy] = useState(false);
  const jobs = (wo.jobs as any[]).filter((j) => !j.is_hold);
  const underWay = jobs.some((j) => ["in_progress", "paused"].includes(j.status));
  const scheduled = jobs.filter((j) => ["planned", "needs_reassignment"].includes(j.status)).length;
  const reserved = (wo.parts?.lines ?? []).filter((p: any) => p.reserved > 0).length;
  return (
    <Modal title={`Delete work order #${wo.number}?`} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button>
        <Button variant="danger" busy={busy} disabled={underWay} onClick={async () => {
          setBusy(true);
          const r = await run("work_orders.delete", { id: wo.id }, { ok: `Work order #${wo.number} was deleted.` });
          setBusy(false);
          if (r) onDeleted();
        }}>Delete permanently</Button></>}>
      <div className="space-y-3 text-sm">
        <p>This permanently deletes work order #{wo.number} for <b>{wo.customer_name}</b> ({wo.vehicle_title}). It can't be undone.</p>
        <ul className="list-disc space-y-1 pl-5 text-slate-700">
          <li>{wo.lines.length} service line{wo.lines.length === 1 ? "" : "s"}</li>
          <li>{scheduled ? `${scheduled} scheduled job${scheduled > 1 ? "s come" : " comes"} off the calendar, freeing the mechanic and bay` : "Nothing on the calendar to remove"}</li>
          <li>{reserved ? "Reserved parts go back to available stock" : "No reserved parts"}</li>
        </ul>
        <p className="text-slate-500">Parts already used stay deducted from stock. The customer and vehicle are kept. The deletion is recorded in History with your name.</p>
        {underWay
          ? <Notice tone="red">Work is under way on this work order. Finish the job or cancel the work order first.</Notice>
          : wo.status !== "cancelled" && wo.status !== "completed" && <Notice>If you only want to stop the work, use Cancel instead: a cancelled work order stays on record.</Notice>}
      </div>
    </Modal>
  );
}

function AddLineModal({ workOrderId, services, onClose }: { workOrderId: string; services: any[]; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ service_type_id: "", description: "", price: "" });
  return (
    <Modal title="Add a line" onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.service_type_id && !f.description.trim()} onClick={async () => {
        const r = await run("work_orders.add_line", { work_order_id: workOrderId, service_type_id: f.service_type_id || null, description: f.description || null, price: f.price ? Number(f.price) : null }, { ok: "Line added." });
        if (r !== undefined) onClose();
      }}>Add</Button></>}>
      <div className="space-y-3">
        <Field label="Service from the catalog"><Select value={f.service_type_id} onChange={set("service_type_id")} placeholder="— custom line —" options={services.filter((s) => s.active).map((s) => [s.id, `${s.name} (${fmtMoney(s.price)})`] as const)} /></Field>
        {!f.service_type_id && <Field label="Description"><Input value={f.description} onChange={set("description")} placeholder="e.g. Replace wiper blades" /></Field>}
        <Field label="Price" hint={f.service_type_id ? "Leave empty to use the catalog price." : undefined}><Input inputMode="decimal" value={f.price} onChange={set("price")} /></Field>
      </div>
    </Modal>
  );
}

function EditModal({ wo, onClose }: { wo: any; onClose: () => void }) {
  const run = useRun();
  const employees = useQ<any[]>("employees.list");
  const [f, set] = useForm({ concern: wo.concern ?? "", notes: wo.notes ?? "", promised_at: wo.promised_at?.slice(0, 16) ?? "", preferred_employee_id: wo.preferred_employee_id ?? "" });
  return (
    <Modal title={`Edit work order #${wo.number}`} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={async () => {
        const r = await run("work_orders.update", { id: wo.id, concern: f.concern || null, notes: f.notes || null, promised_at: f.promised_at ? f.promised_at + ":00" : null, preferred_employee_id: f.preferred_employee_id || null }, { ok: "Saved." });
        if (r !== undefined) onClose();
      }}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Concern"><Textarea value={f.concern} onChange={set("concern")} /></Field>
        <Field label="Notes"><Textarea value={f.notes} onChange={set("notes")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Promised to customer by"><Input type="datetime-local" value={f.promised_at} onChange={set("promised_at")} /></Field>
          <Field label="Preferred mechanic"><Select value={f.preferred_employee_id} onChange={set("preferred_employee_id")} placeholder="No preference" options={(employees.data ?? []).filter((e) => e.active && ["tech", "apprentice"].includes(e.kind)).map((e) => [e.id, e.name] as const)} /></Field>
        </div>
      </div>
    </Modal>
  );
}
