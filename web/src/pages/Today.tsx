// The landing page: what needs attention right now.
import { Link } from "react-router-dom";
import { useCan, useMe, useNow, useQ } from "../store";
import { addDays, fmtDate, fmtDay, fmtTime, iso, parse, startOfDay } from "../lib";
import { Badge, Card, Empty, Loading, PageHeader, StatusBadge } from "../ui";

export function TodayPage() {
  const me = useMe();
  const can = useCan();
  const now = useNow();
  const start = startOfDay(now);
  const feed = useQ<any[]>("calendar.feed", { start: iso(start), end: iso(addDays(start, 1)), kinds: ["job", "event", "delivery", "promised"] }, { enabled: can.view("schedule"), refetchInterval: 60_000 });
  const board = useQ<any[]>("bays.board", {}, { enabled: can.view("bays"), refetchInterval: 60_000 });
  const problems = useQ<any[]>("work_orders.list", { status: "open" }, { enabled: can.view("work_orders") });
  const low = useQ<any[]>("inventory.reorder_suggestions", {}, { enabled: can.view("inventory") });
  const team = useQ<any[]>("employees.list", {}, { enabled: can.view("team") });
  const intake = useQ<any[]>("intake.list", { status: "pending" }, { enabled: can.view("intake") });

  const jobs = (feed.data ?? []).filter((i) => i.kind === "job");
  const mine = jobs.filter((j) => j.employee_id && j.employee_id === me.user.employee_id);
  const flagged = (problems.data ?? []).filter((w) => w.needs_reassignment > 0);
  const unscheduled = (problems.data ?? []).filter((w) => w.status === "draft");
  const certs = (team.data ?? []).flatMap((e) => e.certs.filter((c: any) => c.status !== "valid" && e.active).map((c: any) => ({ ...c, employee: e.name })));
  // A bay is in use when a job currently holds it, whether or not work is actively underway.
  const inUse = (board.data ?? []).filter((b) => ["occupied", "paused", "holding", "scheduled_now"].includes(b.status)).length;

  return (
    <div>
      <PageHeader title={`${now.getHours() < 12 ? "Good morning" : now.getHours() < 18 ? "Good afternoon" : "Good evening"}, ${me.user.name.split(" ")[0]}`}
        subtitle={fmtDay(now, { weekday: "long", month: "long", day: "numeric" })} />

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Jobs today" value={jobs.length} to="/calendar?view=day" />
        <Stat label="Bays in use" value={board.data ? `${inUse} of ${board.data.length}` : "—"} to="/bays" />
        <Stat label="Waiting for review" value={intake.data?.length ?? "—"} to="/intake" tone={intake.data?.length ? "teal" : undefined} />
        <Stat label="Need attention" value={flagged.length + certs.filter((c) => c.status === "expired").length} to="/work-orders" tone={flagged.length ? "red" : undefined} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={me.user.role === "mechanic" ? "My jobs today" : "Today's schedule"} pad={false} actions={<Link className="link text-sm" to="/calendar?view=day">Open calendar</Link>}>
          {feed.isLoading ? <Loading /> : (me.user.role === "mechanic" ? mine : feed.data ?? []).length === 0 ? <Empty title="Nothing scheduled today" /> : (
            <ul className="divide-y divide-slate-100">
              {(me.user.role === "mechanic" ? mine : feed.data ?? []).map((i) => (
                <li key={i.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span className="w-28 shrink-0 text-xs text-slate-500">{fmtTime(i.start)} – {fmtTime(i.end)}</span>
                  <span className="min-w-0 flex-1 truncate">
                    {i.work_order_id ? <Link className="font-medium text-slate-900 hover:underline" to={`/work-orders/${i.work_order_id}`}>{i.title}</Link> : <span className="font-medium text-slate-900">{i.title}</span>}
                    <span className="ml-2 text-slate-500">{[i.employee_name, i.bay_name].filter(Boolean).join(" · ")}</span>
                  </span>
                  {i.status && <StatusBadge status={i.status} />}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-4">
          {flagged.length > 0 && (
            <Card title="Jobs that need reassigning" pad={false}>
              <ul className="divide-y divide-slate-100">
                {flagged.map((w) => (
                  <li key={w.id} className="px-4 py-2 text-sm"><Link className="link font-medium" to={`/work-orders/${w.id}`}>#{w.number} {w.vehicle_title}</Link> <span className="text-slate-500">· {w.customer_name}</span> <Badge tone="red">{w.needs_reassignment} job{w.needs_reassignment > 1 ? "s" : ""}</Badge></li>
                ))}
              </ul>
            </Card>
          )}
          {can.view("work_orders") && (
            <Card title="Not yet scheduled" pad={false}>
              {unscheduled.length === 0 ? <Empty title="Every open work order is on the schedule" /> : (
                <ul className="divide-y divide-slate-100">
                  {unscheduled.slice(0, 6).map((w) => (
                    <li key={w.id} className="flex justify-between px-4 py-2 text-sm"><Link className="link font-medium" to={`/work-orders/${w.id}`}>#{w.number} {w.vehicle_title}</Link><span className="text-slate-500">{w.customer_name}</span></li>
                  ))}
                </ul>
              )}
            </Card>
          )}
          {can.view("team") && certs.length > 0 && (
            <Card title="Certifications expiring" pad={false}>
              <ul className="divide-y divide-slate-100">
                {certs.slice(0, 6).map((c) => (
                  <li key={c.id} className="flex items-center justify-between px-4 py-2 text-sm"><span><b className="font-medium">{c.employee}</b> · {c.name}</span><span className="flex items-center gap-2 text-slate-500">{fmtDate(c.expires_on)} <StatusBadge status={c.status} /></span></li>
                ))}
              </ul>
            </Card>
          )}
          {can.view("inventory") && (low.data?.length ?? 0) > 0 && (
            <Card title="Parts to reorder" pad={false} actions={<Link className="link text-sm" to="/inventory">Inventory</Link>}>
              <ul className="divide-y divide-slate-100">
                {low.data!.slice(0, 6).map((p) => (
                  <li key={p.id} className="flex justify-between px-4 py-2 text-sm"><span><b className="font-medium">{p.name}</b> <span className="text-slate-500">{p.sku}</span></span><span className="text-slate-500">{p.available} available · order {p.suggested_qty}</span></li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, to, tone }: { label: string; value: any; to: string; tone?: "red" | "teal" }) {
  return (
    <Link to={to} className="card block p-4 hover:border-brand-500">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${tone === "red" ? "text-red-700" : tone === "teal" ? "text-brand-700" : "text-slate-900"}`}>{value}</div>
    </Link>
  );
}

export const _unused = parse;
