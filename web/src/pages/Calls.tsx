import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { upload } from "../api";
import { useCan, useQ, useRefresh, useRun } from "../store";
import { fmtDateTime } from "../lib";
import { AccessBanner, CustomerPicker } from "../parts/shared";
import { Badge, Button, Card, Checkbox, Drawer, Empty, Field, Input, Loading, Modal, Notice, PageHeader, StatusBadge, Tabs, Textarea, useForm, useToast } from "../ui";

export function CallsPage() {
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("all");
  const calls = useQ<any[]>("calls.list", { unassigned: tab === "unassigned" });
  const [adding, setAdding] = useState(false);
  const selected = params.get("call");
  return (
    <div>
      <PageHeader title="Calls" subtitle="Phone-call transcripts. Requests found in a call go to the intake queue for you to review."
        actions={can.edit("communications") && <Button variant="primary" onClick={() => setAdding(true)}>+ Add transcript</Button>} />
      <AccessBanner vertical="communications" />
      <Tabs value={tab} onChange={setTab} tabs={[["all", "All calls"], ["unassigned", "Not matched to a customer"]]} />
      <div className="card">
        {calls.isLoading ? <Loading /> : !calls.data?.length ? (
          <Empty title={tab === "unassigned" ? "Every call is matched to a customer" : "No calls yet"}>Connect your phone system under Settings → Integrations to receive transcripts automatically, or paste one in with “Add transcript”.</Empty>
        ) : (
          <ul className="divide-y divide-slate-100">
            {calls.data.map((c) => (
              <li key={c.id}>
                <button className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left hover:bg-slate-50" onClick={() => setParams({ call: c.id })}>
                  <span className="w-40 shrink-0 text-sm text-slate-500">{fmtDateTime(c.started_at)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="font-medium text-slate-900">{c.customer_name ?? c.from_number ?? "Unknown caller"}</span>
                    <span className="block truncate text-sm text-slate-600">{c.summary ?? "No summary yet"}</span>
                  </span>
                  {!c.customer_id && <Badge tone="amber">unmatched</Badge>}
                  <Badge>{c.direction}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {selected && <CallDrawer id={selected} onClose={() => setParams({})} />}
      {adding && <AddTranscript onClose={() => setAdding(false)} />}
    </div>
  );
}

function CallDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const can = useCan();
  const run = useRun();
  const call = useQ<any>("calls.get", { id });
  const [customer, setCustomer] = useState<any | null>(null);
  if (call.isLoading) return <Drawer title="Call" onClose={onClose}><Loading /></Drawer>;
  if (!call.data) return null;
  const c = call.data;
  return (
    <Drawer title={`Call ${fmtDateTime(c.started_at)}`} onClose={onClose}>
      <dl className="mb-4 grid grid-cols-[6rem_1fr] gap-y-1 text-sm">
        <dt className="text-slate-500">Customer</dt><dd>{c.customer_id ? <Link className="link" to={`/customers/${c.customer_id}`}>{c.customer_name}</Link> : <Badge tone="amber">not matched</Badge>}</dd>
        <dt className="text-slate-500">From</dt><dd>{c.from_number ?? "—"}</dd>
        <dt className="text-slate-500">Length</dt><dd>{c.duration_s ? `${Math.round(c.duration_s / 60)} min` : "—"}</dd>
        <dt className="text-slate-500">Source</dt><dd>{c.provider === "manual" ? "Added by hand" : c.provider}</dd>
        <dt className="text-slate-500">Consent</dt><dd>{c.consent_noted ? "Recording consent noted" : "Not recorded"}</dd>
      </dl>
      {c.summary && <Notice tone="blue" className="mb-3"><b>Summary:</b> {c.summary}</Notice>}
      {c.draft && <Notice tone={c.draft.status === "pending" ? "amber" : "green"} className="mb-3">A draft intake from this call is <StatusBadge status={c.draft.status} />. {c.draft.status === "pending" && <Link className="link" to={`/intake/new?submission=${c.draft.id}`}>Review it</Link>}</Notice>}

      {!c.customer_id && can.edit("communications") && (
        <div className="mb-4 rounded-md border border-slate-200 p-3">
          <p className="mb-2 text-sm font-medium">Match this call to a customer</p>
          <CustomerPicker value={customer} onChange={setCustomer} />
          {customer && <Button className="mt-2" variant="primary" onClick={() => run("calls.assign", { id, customer_id: customer.id }, { ok: "Call linked to the customer." })}>Link call</Button>}
        </div>
      )}

      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Transcript</h3>
      <div className="space-y-2">
        {c.segments.map((s: any, i: number) => (
          <div key={i} className="text-sm"><span className="font-semibold text-slate-700">{s.speaker}: </span><span className="text-slate-700">{s.text}</span></div>
        ))}
      </div>
      {can.manager && <Button variant="danger" className="mt-6" onClick={async () => { if (confirm("Permanently delete this call and its transcript?") && (await run("calls.delete", { id }, { ok: "Call deleted." }))) onClose(); }}>Delete call</Button>}
    </Drawer>
  );
}

function AddTranscript({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh();
  const [f, set] = useForm({ text: "", from_number: "", consent_noted: false });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const form = new FormData();
      if (file) form.append("file", file); else form.append("text", f.text);
      form.append("from_number", f.from_number);
      form.append("consent_noted", String(f.consent_noted));
      const res = await upload("/api/calls/upload", form);
      toast(res.status === "pending" ? "Sent for approval." : res.result.customer_name ? `Saved and matched to ${res.result.customer_name}.` : "Saved. It isn't matched to a customer yet.");
      refresh();
      onClose();
    } catch (e: any) {
      toast(e?.message ?? "Couldn't save the transcript.", "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal wide title="Add a call transcript" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={!file && !f.text.trim()} onClick={save}>Save transcript</Button></>}>
      <div className="space-y-3">
        <Field label="Paste the transcript" hint='Lines like "Customer: ..." keep their speaker.'><Textarea rows={8} value={f.text} onChange={set("text")} disabled={!!file} placeholder={"Shop: Thanks for calling, how can I help?\nCustomer: My truck is making a grinding noise when I brake…"} /></Field>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-500">or</span>
          <input type="file" accept=".txt,.vtt,.json" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <Field label="Caller's number" hint="Used to match the call to a customer."><Input type="tel" className="max-w-xs" value={f.from_number} onChange={set("from_number")} /></Field>
        <Checkbox label="The caller was told the call was recorded" checked={f.consent_noted} onChange={set("consent_noted")} />
        <Notice tone="blue">Call-recording consent rules differ by state. Make sure your phone system gives the notice your state requires.</Notice>
      </div>
    </Modal>
  );
}

export const _keep = Card;
