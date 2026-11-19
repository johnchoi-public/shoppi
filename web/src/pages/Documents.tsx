import { useState } from "react";
import { upload } from "../api";
import { useQ, useRefresh, useRun } from "../store";
import { fmtDateTime } from "../lib";
import { AccessBanner } from "../parts/shared";
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, PageHeader, StatusBadge, Table, Textarea, useForm, useToast } from "../ui";

export function DocumentsPage() {
  const run = useRun();
  const toast = useToast();
  const refresh = useRefresh();
  const docs = useQ<any[]>("documents.list");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(false);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const hits = useQ<any[]>("documents.search", { query, k: 5 }, { enabled: query.length >= 3 });

  async function send(file: File) {
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await upload("/api/documents/upload", form);
      toast(res.status === "pending" ? "Sent for approval." : res.result.status === "indexed" ? `Added “${res.result.title}”.` : `Saved “${res.result.title}”, but it couldn't be indexed for search yet.`, res.result?.status === "failed" ? "error" : "ok");
      refresh();
    } catch (e: any) {
      toast(e?.message ?? "Upload failed.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Documents" subtitle="Manuals, policies, warranty terms and notes. The assistant searches these to answer questions."
        actions={<>
          <Button onClick={() => setNote(true)}>+ Write a note</Button>
          <label className="inline-flex cursor-pointer items-center rounded-md bg-brand-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-800">
            {busy ? "Uploading…" : "Upload document"}
            <input type="file" accept=".pdf,.txt,.md,.csv" className="hidden" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }} />
          </label>
        </>} />
      <AccessBanner vertical="documents" />

      <Card title="Search" className="mb-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(text.trim()); }}>
          <Input placeholder="e.g. What is our warranty on brake pads?" value={text} onChange={(e) => setText(e.target.value)} />
          <Button type="submit" variant="primary" disabled={text.trim().length < 3}>Search</Button>
        </form>
        {query && (hits.isLoading ? <Loading /> : hits.isError ? <p className="mt-3 text-sm text-red-700">{(hits.error as any)?.message}</p> : (
          <ul className="mt-3 space-y-2">
            {(hits.data ?? []).map((h, i) => (
              <li key={i} className="rounded-md border border-slate-200 p-3 text-sm">
                <div className="mb-1 flex items-center gap-2 text-xs text-slate-500"><b className="text-slate-700">{h.source}</b>{h.kind === "call_transcript" && <Badge tone="blue">phone call</Badge>}</div>
                <p className="whitespace-pre-wrap text-slate-700">{h.text}</p>
              </li>
            ))}
            {!hits.data?.length && <li className="text-sm text-slate-500">Nothing relevant found.</li>}
          </ul>
        ))}
      </Card>

      <div className="card">
        {docs.isLoading ? <Loading /> : (
          <Table head={["Title", "Type", "Passages", "Added", "Status", ""]} empty={!docs.data?.length && <Empty title="No documents yet">Upload PDFs or text files (service manuals, shop policies, warranty terms). They're split into passages and indexed so you and the assistant can search them.</Empty>}>
            {(docs.data ?? []).map((d) => (
              <tr key={d.id}>
                <td className="td font-medium">{d.title}{d.error && <div className="text-xs font-normal text-red-600">{d.error}</div>}</td>
                <td className="td">{d.kind === "note" ? "Note" : d.filename?.split(".").pop()?.toUpperCase() ?? "File"}</td>
                <td className="td">{d.chunk_count}</td><td className="td text-slate-500">{fmtDateTime(d.created_at)}</td><td className="td"><StatusBadge status={d.status} /></td>
                <td className="td text-right"><Button size="sm" variant="ghost" onClick={() => confirm(`Remove “${d.title}”?`) && run("documents.delete", { id: d.id }, { ok: "Document removed." })}>Remove</Button></td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      {note && <NoteModal onClose={() => setNote(false)} />}
    </div>
  );
}

function NoteModal({ onClose }: { onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ title: "", text: "" });
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="Write a note" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={!f.title.trim() || !f.text.trim()} onClick={async () => {
      setBusy(true);
      const r = await run("documents.add", { title: f.title, text: f.text, kind: "note" }, { ok: "Note saved." });
      setBusy(false);
      if (r !== undefined) onClose();
    }}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Title"><Input autoFocus value={f.title} onChange={set("title")} placeholder="e.g. Loaner car policy" /></Field>
        <Field label="Text"><Textarea rows={8} value={f.text} onChange={set("text")} /></Field>
      </div>
    </Modal>
  );
}
