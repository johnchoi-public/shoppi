import { useState } from "react";
import { upload } from "../api";
import { useCan, useQ, useRefresh, useRun } from "../store";
import { fmtDateTime, fmtMoney, fromInput } from "../lib";
import { AccessBanner } from "../parts/shared";
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, useForm, useToast } from "../ui";

export function InventoryPage() {
  const [tab, setTab] = useState("parts");
  const low = useQ<any[]>("inventory.reorder_suggestions");
  return (
    <div>
      <PageHeader title="Inventory" subtitle="Parts on hand, what is reserved for jobs, and what is on order." />
      <AccessBanner vertical="inventory" />
      <Tabs value={tab} onChange={setTab} tabs={[["parts", "Parts"], ["reorder", `Reorder${low.data?.length ? ` (${low.data.length})` : ""}`], ["orders", "Purchase orders"], ["catalog", "Find parts"], ["suppliers", "Suppliers"]]} />
      {tab === "parts" && <Parts />}
      {tab === "reorder" && <Reorder rows={low.data ?? []} />}
      {tab === "orders" && <Orders />}
      {tab === "catalog" && <FindParts />}
      {tab === "suppliers" && <Suppliers />}
    </div>
  );
}

function Parts() {
  const [text, setText] = useState("");
  const parts = useQ<any[]>("parts.list", { q: text });
  const [editing, setEditing] = useState<any | "new" | null>(null);
  const [adjusting, setAdjusting] = useState<any | null>(null);
  return (
    <>
      <div className="mb-3 flex justify-between gap-2">
        <Input className="max-w-xs" placeholder="Search SKU, name or brand…" value={text} onChange={(e) => setText(e.target.value)} />
        <Button variant="primary" onClick={() => setEditing("new")}>+ Add part</Button>
      </div>
      <div className="card">
        {parts.isLoading ? <Loading /> : (
          <Table head={["SKU", "Part", "On hand", "Reserved", "Available", "On order", "Reorder at", "Cost", ""]} empty={!parts.data?.length && <Empty title="No parts yet">Add the parts you stock, or pull them in from a catalog under Find parts.</Empty>}>
            {(parts.data ?? []).map((p) => (
              <tr key={p.id}>
                <td className="td font-mono text-xs">{p.sku}</td>
                <td className="td"><span className="font-medium text-slate-900">{p.name}</span>{p.brand && <span className="ml-1 text-slate-500">{p.brand}</span>}{p.location && <div className="text-xs text-slate-400">{p.location}</div>}</td>
                <td className="td">{p.on_hand}</td><td className="td">{p.reserved || "—"}</td>
                <td className="td font-medium">{p.available} {p.low && <Badge tone="amber">low</Badge>}</td>
                <td className="td">{p.on_order || "—"}</td><td className="td">{p.reorder_point || "—"}</td><td className="td">{fmtMoney(p.unit_cost)}</td>
                <td className="td whitespace-nowrap text-right"><Button size="sm" onClick={() => setAdjusting(p)}>Adjust</Button> <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>Edit</Button></td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      {editing && <PartModal part={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {adjusting && <AdjustModal part={adjusting} onClose={() => setAdjusting(null)} />}
    </>
  );
}

function PartModal({ part, onClose }: { part?: any; onClose: () => void }) {
  const run = useRun();
  const suppliers = useQ<any[]>("suppliers.list");
  const s = (v: any) => (v == null ? "" : String(v));
  const [f, set] = useForm({ sku: s(part?.sku), name: s(part?.name), brand: s(part?.brand), on_hand: s(part?.on_hand ?? 0), reorder_point: s(part?.reorder_point ?? 0), reorder_qty: s(part?.reorder_qty ?? 0),
    unit_cost: s(part?.unit_cost), unit_price: s(part?.unit_price), location: s(part?.location), supplier_id: s(part?.supplier_id) });
  const num = (v: string) => (v === "" ? null : Number(v));
  async function save() {
    const base = { sku: f.sku, name: f.name, brand: f.brand || null, reorder_point: Number(f.reorder_point || 0), reorder_qty: Number(f.reorder_qty || 0), unit_cost: num(f.unit_cost), unit_price: num(f.unit_price), location: f.location || null, supplier_id: f.supplier_id || null };
    const r = part ? await run("parts.update", { id: part.id, ...base }, { ok: "Part saved." }) : await run("parts.create", { ...base, on_hand: Number(f.on_hand || 0) }, { ok: "Part added." });
    if (r !== undefined) onClose();
  }
  return (
    <Modal title={part ? "Edit part" : "Add part"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.sku.trim() || !f.name.trim()} onClick={save}>Save</Button></>}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="SKU / part number"><Input autoFocus value={f.sku} onChange={set("sku")} /></Field>
        <Field label="Brand"><Input value={f.brand} onChange={set("brand")} /></Field>
        <Field label="Name" className="col-span-2"><Input value={f.name} onChange={set("name")} /></Field>
        {!part && <Field label="On hand now"><Input inputMode="numeric" value={f.on_hand} onChange={set("on_hand")} /></Field>}
        <Field label="Shelf location"><Input value={f.location} onChange={set("location")} /></Field>
        <Field label="Reorder when available falls to"><Input inputMode="numeric" value={f.reorder_point} onChange={set("reorder_point")} /></Field>
        <Field label="Usual order quantity"><Input inputMode="numeric" value={f.reorder_qty} onChange={set("reorder_qty")} /></Field>
        <Field label="Cost"><Input inputMode="decimal" value={f.unit_cost} onChange={set("unit_cost")} /></Field>
        <Field label="Price"><Input inputMode="decimal" value={f.unit_price} onChange={set("unit_price")} /></Field>
        <Field label="Supplier" className="col-span-2"><Select value={f.supplier_id} onChange={set("supplier_id")} placeholder="None" options={(suppliers.data ?? []).map((x) => [x.id, x.name] as const)} /></Field>
      </div>
    </Modal>
  );
}

function AdjustModal({ part, onClose }: { part: any; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ delta: "", reason: "adjust", note: "" });
  return (
    <Modal title={`Adjust stock: ${part.name}`} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!Number(f.delta)} onClick={async () => {
        const r = await run("parts.adjust", { part_id: part.id, delta: Number(f.delta), reason: f.reason, note: f.note || null }, { ok: "Stock updated." });
        if (r !== undefined) onClose();
      }}>Save</Button></>}>
      <p className="mb-3 text-sm text-slate-600">{part.on_hand} on hand now.</p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Change" hint="Use a minus sign to remove stock."><Input autoFocus inputMode="numeric" value={f.delta} onChange={set("delta")} placeholder="e.g. 5 or -2" /></Field>
        <Field label="Reason"><Select value={f.reason} onChange={set("reason")} options={[["adjust", "Count correction"], ["receive", "Received"], ["return", "Returned to supplier"], ["use", "Used / damaged"]]} /></Field>
        <Field label="Note" className="col-span-2"><Input value={f.note} onChange={set("note")} /></Field>
      </div>
    </Modal>
  );
}

function Reorder({ rows }: { rows: any[] }) {
  const run = useRun();
  if (!rows.length) return <div className="card"><Empty title="Nothing needs reordering">Parts appear here when what's available plus what's on order falls to their reorder level.</Empty></div>;
  return (
    <div className="card">
      <div className="flex justify-end border-b border-slate-100 p-3">
        <Button variant="primary" onClick={() => run("purchase_orders.create", { lines: rows.map((p) => ({ part_id: p.id, qty: p.suggested_qty })) }, { ok: "Draft purchase order created. Find it under Purchase orders." })}>Draft a purchase order for all</Button>
      </div>
      <Table head={["Part", "Available", "On order", "Reorder at", "Suggested", "Supplier"]}>
        {rows.map((p) => <tr key={p.id}><td className="td font-medium">{p.name} <span className="text-xs text-slate-400">{p.sku}</span></td><td className="td">{p.available}</td><td className="td">{p.on_order}</td><td className="td">{p.reorder_point}</td><td className="td font-medium">{p.suggested_qty}</td><td className="td">{p.supplier_name ?? "—"}</td></tr>)}
      </Table>
    </div>
  );
}

function Orders() {
  const can = useCan();
  const run = useRun();
  const orders = useQ<any[]>("purchase_orders.list");
  const integrations = useQ<any[]>("integrations.list", {}, { enabled: can.manager });
  const [ordering, setOrdering] = useState<any | null>(null);
  const [placing, setPlacing] = useState<any | null>(null);
  const catalogs = (integrations.data ?? []).filter((i) => i.category === "parts" && i.enabled && i.supports_ordering);
  if (orders.isLoading) return <Loading />;
  if (!orders.data?.length) return <div className="card"><Empty title="No purchase orders yet">Draft one from the Reorder tab or from Find parts.</Empty></div>;
  return (
    <div className="space-y-3">
      {orders.data.map((po) => (
        <Card key={po.id} pad={false} title={<span className="flex items-center gap-2">PO #{po.number} <StatusBadge status={po.status} /> <span className="font-normal text-slate-500">{po.supplier_name ?? "No supplier"}{po.external_ref && ` · ref ${po.external_ref}`}</span></span>}
          actions={<>
            {po.status === "draft" && <Button size="sm" onClick={() => setOrdering(po)}>Mark as ordered</Button>}
            {po.status === "draft" && catalogs.length > 0 && <Button size="sm" variant="primary" onClick={() => setPlacing(po)}>Order from supplier…</Button>}
            {po.status === "ordered" && <Button size="sm" variant="primary" onClick={() => run("purchase_orders.receive", { id: po.id }, { ok: "Received into stock." })}>Receive all</Button>}
            {["draft", "ordered"].includes(po.status) && <Button size="sm" variant="ghost" onClick={() => run("purchase_orders.cancel", { id: po.id }, { ok: "Purchase order cancelled." })}>Cancel</Button>}
          </>}>
          <Table head={["Part", "Ordered", "Received", "Unit cost"]}>
            {po.lines.map((l: any) => <tr key={l.id}><td className="td">{l.name} <span className="text-xs text-slate-400">{l.sku}</span></td><td className="td">{l.qty}</td><td className="td">{l.received_qty}</td><td className="td">{fmtMoney(l.unit_cost)}</td></tr>)}
          </Table>
          <div className="flex justify-between border-t border-slate-100 px-4 py-2 text-sm text-slate-600"><span>{po.eta ? `Expected ${fmtDateTime(po.eta)}` : "No delivery date"}</span><span className="font-medium">Total {fmtMoney(po.total)}</span></div>
        </Card>
      ))}
      {ordering && <OrderedModal po={ordering} onClose={() => setOrdering(null)} />}
      {placing && <PlaceOrderModal po={placing} catalogs={catalogs} onClose={() => setPlacing(null)} />}
    </div>
  );
}

function OrderedModal({ po, onClose }: { po: any; onClose: () => void }) {
  const run = useRun();
  const [eta, setEta] = useState(po.eta?.slice(0, 16) ?? "");
  return (
    <Modal title={`PO #${po.number}: mark as ordered`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={async () => { if ((await run("purchase_orders.mark_ordered", { id: po.id, eta: fromInput(eta) }, { ok: "Marked as ordered." })) !== undefined) onClose(); }}>Save</Button></>}>
      <Field label="Expected delivery" hint="Jobs that need these parts won't be offered times before this."><Input type="datetime-local" value={eta} onChange={(e) => setEta(e.target.value)} /></Field>
    </Modal>
  );
}

/** Placing a supplier order is a purchase: show exactly what will be sent and the total, and require an explicit confirmation. */
function PlaceOrderModal({ po, catalogs, onClose }: { po: any; catalogs: any[]; onClose: () => void }) {
  const run = useRun();
  const [provider, setProvider] = useState(catalogs[0]?.key ?? "");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={`Order PO #${po.number} from a supplier`} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={!provider} onClick={async () => {
        setBusy(true);
        const r: any = await run("inventory.place_supplier_order", { po_id: po.id, provider, confirm_total: po.total }, { ok: "Order placed with the supplier." });
        setBusy(false);
        if (r) onClose();
      }}>Place order for {fmtMoney(po.total)}</Button></>}>
      <Notice className="mb-3">This sends a real order to the supplier.</Notice>
      <Field label="Supplier catalog"><Select value={provider} onChange={(e) => setProvider(e.target.value)} options={catalogs.map((c) => [c.key, c.name] as const)} /></Field>
      <ul className="mt-3 divide-y divide-slate-100 rounded-md border border-slate-200 text-sm">
        {po.lines.map((l: any) => <li key={l.id} className="flex justify-between px-3 py-1.5"><span>{l.qty} × {l.name}</span><span>{fmtMoney((l.unit_cost ?? 0) * l.qty)}</span></li>)}
        <li className="flex justify-between px-3 py-1.5 font-semibold"><span>Total</span><span>{fmtMoney(po.total)}</span></li>
      </ul>
    </Modal>
  );
}

function Suppliers() {
  const run = useRun();
  const suppliers = useQ<any[]>("suppliers.list");
  const [editing, setEditing] = useState<any | "new" | null>(null);
  return (
    <>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setEditing("new")}>+ Add supplier</Button></div>
      <div className="card">
        <Table head={["Name", "Phone", "Email", "Account", ""]} empty={!suppliers.data?.length && <Empty title="No suppliers yet" />}>
          {(suppliers.data ?? []).map((s) => <tr key={s.id}><td className="td font-medium">{s.name}</td><td className="td">{s.phone ?? "—"}</td><td className="td">{s.email ?? "—"}</td><td className="td">{s.account_number ?? "—"}</td><td className="td text-right"><Button size="sm" variant="ghost" onClick={() => setEditing(s)}>Edit</Button></td></tr>)}
        </Table>
      </div>
      {editing && <SupplierModal supplier={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} run={run} />}
    </>
  );
}

function SupplierModal({ supplier, onClose, run }: { supplier?: any; onClose: () => void; run: any }) {
  const [f, set] = useForm({ name: supplier?.name ?? "", phone: supplier?.phone ?? "", email: supplier?.email ?? "", account_number: supplier?.account_number ?? "" });
  return (
    <Modal title={supplier ? "Edit supplier" : "Add supplier"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.name.trim()} onClick={async () => {
      if ((await run("suppliers.save", { id: supplier?.id, name: f.name, phone: f.phone || null, email: f.email || null, account_number: f.account_number || null }, { ok: "Supplier saved." })) !== undefined) onClose();
    }}>Save</Button></>}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name" className="col-span-2"><Input autoFocus value={f.name} onChange={set("name")} /></Field>
        <Field label="Phone"><Input value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Email"><Input value={f.email} onChange={set("email")} /></Field>
        <Field label="Account number" className="col-span-2"><Input value={f.account_number} onChange={set("account_number")} /></Field>
      </div>
    </Modal>
  );
}

// ---- Catalog search ---------------------------------------------------------------

/** Search connected parts catalogs. On a work order it searches for that vehicle. */
export function FindParts({ workOrderId }: { workOrderId?: string }) {
  const run = useRun();
  const toast = useToast();
  const refresh = useRefresh();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const found = useQ<any>("catalog.search", { query, work_order_id: workOrderId }, { enabled: query.length >= 2 });
  const files = useQ<any[]>("catalog.files");
  const [uploading, setUploading] = useState(false);

  async function uploadCsv(file: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await upload("/api/catalog/upload", form);
      toast(res.status === "pending" ? "Sent for approval." : `Imported ${res.result.rows} rows from ${res.result.supplier_name}.`);
      refresh();
    } catch (e: any) {
      toast(e?.message ?? "Upload failed.", "error");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-4">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(text.trim()); }}>
        <Input className="max-w-md" placeholder="Search catalogs: e.g. front brake pads, or a part number" value={text} onChange={(e) => setText(e.target.value)} />
        <Button type="submit" variant="primary" disabled={text.trim().length < 2}>Search</Button>
      </form>
      {found.data?.vehicle && <p className="text-sm text-slate-500">Searching for parts for the <b>{found.data.vehicle}</b>.</p>}
      {found.data?.errors.map((e: any, i: number) => <Notice key={i}>{e.provider}: {e.message}</Notice>)}

      {query && (
        <div className="card">
          {found.isLoading ? <Loading /> : (
            <Table head={["Part", "Supplier", "Fits", "Cost", "List", "Availability", ""]}
              empty={!found.data?.results.length && <Empty title="No matching parts">{found.data?.searched.length ? `Searched: ${found.data.searched.join(", ")}.` : "No parts catalog is switched on. Enable one under Settings → Integrations, or upload a supplier price file below."}</Empty>}>
              {(found.data?.results ?? []).map((r: any, i: number) => (
                <tr key={i}>
                  <td className="td"><span className="font-medium text-slate-900">{r.description}</span><div className="text-xs text-slate-500">{r.brand} · {r.part_number}</div></td>
                  <td className="td">{r.supplier}</td><td className="td max-w-48 text-xs text-slate-500">{r.fitment_notes ?? "—"}</td>
                  <td className="td">{fmtMoney(r.cost)}</td><td className="td">{fmtMoney(r.list_price)}</td>
                  <td className="td whitespace-nowrap">{r.in_stock_qty > 0 ? <Badge tone="green">{r.in_stock_qty} in stock</Badge> : <Badge tone="amber">out of stock</Badge>}{r.best_eta_days != null && <span className="ml-1 text-xs text-slate-500">{r.best_eta_days === 0 ? "today" : `${r.best_eta_days} day${r.best_eta_days > 1 ? "s" : ""}`}</span>}</td>
                  <td className="td text-right">
                    {r.linked_part_id ? <Badge tone="teal">in inventory</Badge> : (
                      <Button size="sm" onClick={() => run("catalog.add_to_inventory", { provider: r.provider, part_number: r.part_number, description: r.description, brand: r.brand, cost: r.cost, list_price: r.list_price, eta_days: r.best_eta_days }, { ok: "Added to inventory and linked to the catalog." })}>Add to inventory</Button>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </div>
      )}

      <Card title="Supplier price files">
        <p className="mb-3 text-sm text-slate-600">Upload a supplier's price list (CSV with a header row: <code>part_number, description, brand, fitment, list_price, cost, qty_available, eta_days</code>) to search it here like an online catalog.</p>
        <label className="inline-flex cursor-pointer items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">
          {uploading ? "Uploading…" : "Upload CSV"}
          <input type="file" accept=".csv,text/csv" className="hidden" disabled={uploading} onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadCsv(f); e.target.value = ""; }} />
        </label>
        {(files.data?.length ?? 0) > 0 && <ul className="mt-3 text-sm text-slate-600">{files.data!.map((f) => <li key={f.supplier_name}>{f.supplier_name}: {f.rows.toLocaleString()} parts, uploaded {fmtDateTime(f.uploaded_at)}</li>)}</ul>}
      </Card>
    </div>
  );
}
