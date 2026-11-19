import { useState } from "react";
import { useQ } from "../store";
import { HistoryList } from "../parts/shared";
import { Button, Loading, PageHeader, Select } from "../ui";

const TYPES: [string, string][] = [
  ["customers", "Customers"], ["vehicles", "Vehicles"], ["work_orders", "Work orders"], ["job_assignments", "Scheduled jobs"], ["calendar_events", "Calendar events"],
  ["shifts", "Shifts"], ["time_off", "Time off"], ["employees", "Employees"], ["employee_certs", "Certifications"], ["bays", "Bays"], ["bay_closures", "Bay closures"],
  ["parts", "Parts"], ["stock_movements", "Stock movements"], ["purchase_orders", "Purchase orders"], ["service_types", "Services"], ["intake_submissions", "Intake submissions"],
  ["permission_grants", "Access grants"], ["change_requests", "Change requests"], ["documents", "Documents"], ["shops", "Shop settings"],
];

export function HistoryPage() {
  const [type, setType] = useState("");
  const [user, setUser] = useState("");
  const [limit, setLimit] = useState(100);
  const members = useQ<any>("members.list");
  const rows = useQ<any[]>("history.list", { entity_type: type, user_id: user, limit });
  return (
    <div>
      <PageHeader title="History" subtitle="Every change made in the shop: who changed what, and when. Visible to everyone on the team." />
      <div className="mb-3 flex flex-wrap gap-2">
        <Select className="w-56" value={type} onChange={(e) => setType(e.target.value)} placeholder="Everything" options={TYPES} />
        <Select className="w-56" value={user} onChange={(e) => setUser(e.target.value)} placeholder="Everyone" options={(members.data?.members ?? []).map((m: any) => [m.user_id, m.name] as const)} />
      </div>
      <div className="card">
        {rows.isLoading ? <Loading /> : <HistoryList rows={rows.data ?? []} />}
        {(rows.data?.length ?? 0) >= limit && <div className="border-t border-slate-100 p-3 text-center"><Button onClick={() => setLimit(limit + 200)}>Show more</Button></div>}
      </div>
    </div>
  );
}
