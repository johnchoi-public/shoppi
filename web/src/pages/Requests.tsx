// Approvals: specific changes proposed by people who can only view an area (or
// drafted by the assistant), and requests for edit access to a whole area.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { post } from "../api";
import { useCan, useMe, useQ, useRun } from "../store";
import { fmtDateTime } from "../lib";
import { Badge, Button, Card, Diff, Empty, Field, Loading, Modal, PageHeader, Select, StatusBadge, Table, Tabs, useForm, useToast } from "../ui";

export function RequestsPage() {
  const can = useCan();
  const [tab, setTab] = useState("changes");
  return (
    <div>
      <PageHeader title="Requests" subtitle={can.manager ? "Approve changes proposed by your team, and decide who may edit what." : "Changes you've proposed and edit access you've asked for."} />
      <Tabs value={tab} onChange={setTab} tabs={[["changes", "Proposed changes"], ["access", "Edit access"]]} />
      {tab === "changes" ? <Changes /> : <Access />}
    </div>
  );
}

function Changes() {
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("pending");
  const list = useQ<any[]>("change_requests.list", { status: status === "all" ? null : status });
  const [busy, setBusy] = useState<string | null>(null);

  async function decide(id: string, approve: boolean) {
    setBusy(id);
    try {
      const res = await post(`/api/change_requests/${id}/decide`, { approve });
      toast(res.status === "applied" ? "Approved and applied." : res.status === "stale" ? "The record changed after this was requested, so it was not applied." : res.status === "cancelled" ? "Cancelled." : "Declined.", res.status === "stale" ? "info" : "ok");
    } catch (e: any) {
      toast(e?.message ?? "Couldn't apply this change.", "error");
    } finally {
      setBusy(null);
      qc.invalidateQueries({ queryKey: ["q"] });
    }
  }

  return (
    <>
      <Select className="mb-3 w-48" value={status} onChange={(e) => setStatus(e.target.value)} options={[["pending", "Waiting"], ["applied", "Approved"], ["rejected", "Declined"], ["stale", "Out of date"], ["cancelled", "Cancelled"], ["all", "All"]]} />
      {list.isLoading ? <Loading /> : !list.data?.length ? <div className="card"><Empty title="Nothing here">When someone with view-only access changes something, or the assistant drafts a change, it waits here for a decision.</Empty></div> : (
        <div className="space-y-3">
          {list.data.map((r) => (
            <Card key={r.id}
              title={<span className="flex flex-wrap items-center gap-2">{r.summary || r.command} <Badge>{r.vertical_label}</Badge>{r.source === "agent" && <Badge tone="violet">drafted by the assistant</Badge>}<StatusBadge status={r.status} /></span>}
              actions={r.status === "pending" && <>
                {r.can_decide && <Button size="sm" variant="primary" busy={busy === r.id} onClick={() => decide(r.id, true)}>{r.needs === "self" && r.can_cancel ? "Confirm" : "Approve"}</Button>}
                {(r.can_decide || r.can_cancel) && <Button size="sm" onClick={() => decide(r.id, false)}>{r.can_cancel ? "Cancel" : "Decline"}</Button>}
              </>}>
              <p className="mb-2 text-xs text-slate-500">Requested by <b>{r.requester_name}</b> · {fmtDateTime(r.created_at)}{r.note && ` · ${r.note}`}</p>
              <div className="space-y-2">
                {(r.preview ?? []).map((p: any, i: number) => (
                  <div key={i} className="rounded border border-slate-100 bg-slate-50 p-2">
                    <div className="mb-1 text-[11px] font-semibold uppercase text-slate-500">{p.op} {p.entity_type.replace(/_/g, " ")}</div>
                    <Diff op={p.op} before={p.before} after={p.after} changed={p.changed_fields} />
                  </div>
                ))}
                {!r.preview?.length && <pre className="overflow-x-auto rounded bg-slate-50 p-2 text-xs text-slate-600">{JSON.stringify(r.args, null, 1)}</pre>}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

function Access() {
  const me = useMe();
  const can = useCan();
  const run = useRun();
  const data = useQ<any>("access.list");
  const members = useQ<any>("members.list", {}, { enabled: can.manager });
  const [granting, setGranting] = useState(false);
  const [asking, setAsking] = useState(false);
  if (data.isLoading) return <Loading />;
  const pending = data.data.requests.filter((r: any) => r.status === "pending");
  const grants = data.data.grants.filter((g: any) => g.active);
  return (
    <div className="space-y-4">
      <Card title="Waiting for a decision" pad={false} actions={!can.manager && <Button size="sm" onClick={() => setAsking(true)}>Request edit access</Button>}>
        {!pending.length ? <Empty title="No pending requests" /> : (
          <Table head={["Who", "Area", "Why", "For", ""]}>
            {pending.map((r: any) => (
              <tr key={r.id}>
                <td className="td font-medium">{r.requester_name}</td><td className="td">{r.vertical_label}</td><td className="td text-slate-600">{r.reason ?? "—"}</td>
                <td className="td">{r.requested_hours ? `${r.requested_hours} h` : "until revoked"}</td>
                <td className="td whitespace-nowrap text-right">{can.manager && <><Button size="sm" variant="primary" onClick={() => run("access.decide", { id: r.id, approve: true }, { ok: "Access granted." })}>Grant</Button> <Button size="sm" onClick={() => run("access.decide", { id: r.id, approve: false }, { ok: "Request denied." })}>Deny</Button></>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Card title="Current edit access" pad={false} actions={can.manager && <Button size="sm" onClick={() => setGranting(true)}>+ Grant access</Button>}>
        {!grants.length ? <Empty title="No extra access granted">Owners and advisors can edit everything. Mechanics can view, and can be given edit rights for individual areas here.</Empty> : (
          <Table head={["Who", "Area", "Until", ""]}>
            {grants.map((g: any) => (
              <tr key={g.id}><td className="td font-medium">{g.user_name}</td><td className="td">{g.vertical_label}</td><td className="td">{g.expires_at ? fmtDateTime(g.expires_at) : "Until revoked"}</td>
                <td className="td text-right">{can.manager && <Button size="sm" variant="danger" onClick={() => run("access.revoke", { id: g.id }, { ok: "Access revoked." })}>Revoke</Button>}</td></tr>
            ))}
          </Table>
        )}
      </Card>
      {granting && <GrantModal members={(members.data?.members ?? []).filter((m: any) => m.role === "mechanic")} onClose={() => setGranting(false)} />}
      {asking && <AskModal verticals={me.grantable.filter((v) => me.permissions[v] !== "edit").map((v) => [v, me.verticals[v]] as const)} onClose={() => setAsking(false)} />}
    </div>
  );
}

const DURATIONS = [["4", "4 hours"], ["8", "8 hours"], ["24", "1 day"], ["168", "1 week"], ["", "Until revoked"]] as const;

function GrantModal({ members, onClose }: { members: any[]; onClose: () => void }) {
  const me = useMe();
  const run = useRun();
  const [f, set] = useForm({ user_id: members[0]?.user_id ?? "", vertical: me.grantable[0], hours: "" });
  return (
    <Modal title="Grant edit access" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.user_id} onClick={async () => {
      if ((await run("access.grant", { user_id: f.user_id, vertical: f.vertical, hours: f.hours ? Number(f.hours) : null }, { ok: "Access granted." })) !== undefined) onClose();
    }}>Grant</Button></>}>
      {!members.length ? <p className="text-sm text-slate-500">There are no mechanics with a login yet. Invite them under Settings → Members.</p> : (
        <div className="space-y-3">
          <Field label="Who"><Select value={f.user_id} onChange={set("user_id")} options={members.map((m) => [m.user_id, m.name] as const)} /></Field>
          <Field label="Area"><Select value={f.vertical} onChange={set("vertical")} options={me.grantable.map((v) => [v, me.verticals[v]] as const)} /></Field>
          <Field label="For how long"><Select value={f.hours} onChange={set("hours")} options={DURATIONS} /></Field>
        </div>
      )}
    </Modal>
  );
}

function AskModal({ verticals, onClose }: { verticals: (readonly [string, string])[]; onClose: () => void }) {
  const run = useRun();
  const [f, set] = useForm({ vertical: verticals[0]?.[0] ?? "", reason: "", hours: "8" });
  return (
    <Modal title="Request edit access" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.vertical} onClick={async () => {
      if ((await run("access.request", { vertical: f.vertical, reason: f.reason || null, hours: f.hours ? Number(f.hours) : null }, { ok: "Request sent." })) !== undefined) onClose();
    }}>Send request</Button></>}>
      <div className="space-y-3">
        <Field label="Area"><Select value={f.vertical} onChange={set("vertical")} options={verticals} /></Field>
        <Field label="Why do you need it?"><input className="input" value={f.reason} onChange={set("reason")} /></Field>
        <Field label="For how long"><Select value={f.hours} onChange={set("hours")} options={DURATIONS} /></Field>
      </div>
    </Modal>
  );
}
