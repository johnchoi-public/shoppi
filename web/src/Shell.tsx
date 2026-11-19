// The main app layout: sidebar navigation, top bar, and the assistant dock.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { post } from "./api";
import { useCan, useMe, useQ } from "./store";
import { cx } from "./lib";
import { ChatDock } from "./parts/ChatDock";
import { Badge, Button } from "./ui";

const NAV: { to: string; label: string; icon: string; vertical?: string; managers?: boolean }[] = [
  { to: "/", label: "Today", icon: "◎" },
  { to: "/calendar", label: "Calendar", icon: "▦", vertical: "schedule" },
  { to: "/bays", label: "Bays", icon: "▤", vertical: "bays" },
  { to: "/intake", label: "Intake", icon: "✎", vertical: "intake" },
  { to: "/customers", label: "Customers", icon: "☺", vertical: "customers" },
  { to: "/work-orders", label: "Work orders", icon: "▣", vertical: "work_orders" },
  { to: "/inventory", label: "Inventory", icon: "❖", vertical: "inventory" },
  { to: "/team", label: "Team", icon: "⚑", vertical: "team" },
  { to: "/documents", label: "Documents", icon: "❏", vertical: "documents" },
  { to: "/calls", label: "Calls", icon: "☏", vertical: "communications" },
  { to: "/history", label: "History", icon: "↺" },
  { to: "/requests", label: "Requests", icon: "✓" },
  { to: "/settings/shop", label: "Settings", icon: "⚙" },
];

export function Shell() {
  const me = useMe();
  const can = useCan();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [chat, setChat] = useState(false);
  const [menu, setMenu] = useState(false);
  const pending = useQ<any[]>("change_requests.list", { status: "pending" }, { refetchInterval: 30_000 });
  const access = useQ<any>("access.list", {}, { enabled: can.manager, refetchInterval: 30_000 });
  const waiting = (pending.data?.filter((r) => r.can_decide).length ?? 0) + (access.data?.requests.filter((r: any) => r.status === "pending").length ?? 0);
  const intake = useQ<any[]>("intake.list", { status: "pending" }, { enabled: can.view("intake"), refetchInterval: 30_000 });

  async function logout() {
    await post("/api/auth/logout");
    qc.clear();
    qc.setQueryData(["me"], null);
    nav("/login");
  }
  async function switchShop(shop_id: string) {
    await post("/api/auth/switch", { shop_id });
    qc.clear();
    location.assign("/");
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-4 py-3.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-700 text-sm font-bold text-white">S</span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-900">{me.shop.name}</div>
            <div className="text-[11px] text-slate-500">Shoppi</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3" aria-label="Main">
          {NAV.filter((n) => !n.vertical || can.view(n.vertical)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => cx("flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                isActive || (n.to.startsWith("/settings") && location.pathname.startsWith("/settings")) ? "bg-brand-50 font-medium text-brand-800" : "text-slate-600 hover:bg-slate-100")}>
              <span className="w-4 text-center text-slate-400" aria-hidden>{n.icon}</span>
              <span className="flex-1">{n.label}</span>
              {n.to === "/requests" && waiting > 0 && <Badge tone="amber">{waiting}</Badge>}
              {n.to === "/intake" && (intake.data?.length ?? 0) > 0 && <Badge tone="teal">{intake.data!.length}</Badge>}
            </NavLink>
          ))}
        </nav>
        <div className="relative border-t border-slate-200 p-2">
          <button onClick={() => setMenu((m) => !m)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-slate-100" aria-expanded={menu}>
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700">{me.user.name.slice(0, 1).toUpperCase()}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-slate-800">{me.user.name}</span>
              <span className="block text-[11px] capitalize text-slate-500">{me.user.role}</span>
            </span>
          </button>
          {menu && (
            <div className="absolute bottom-full left-2 right-2 mb-1 rounded-md border border-slate-200 bg-white py-1 shadow-lg">
              {me.shops.filter((s) => s.shop_id !== me.shop.id).map((s) => (
                <button key={s.shop_id} onClick={() => switchShop(s.shop_id)} className="block w-full px-3 py-1.5 text-left text-sm hover:bg-slate-50">Switch to {s.shop_name}</button>
              ))}
              <button onClick={logout} className="block w-full px-3 py-1.5 text-left text-sm hover:bg-slate-50">Sign out</button>
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end gap-2 border-b border-slate-200 bg-white px-5 py-2">
          {can.edit("intake") && <Button variant="primary" onClick={() => nav("/intake/new")}>+ New customer intake</Button>}
          <Button onClick={() => setChat((c) => !c)} aria-pressed={chat}>{chat ? "Hide assistant" : "✦ Assistant"}</Button>
        </header>
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-y-auto p-5"><Outlet /></main>
          {chat && <ChatDock onClose={() => setChat(false)} />}
        </div>
      </div>
    </div>
  );
}
