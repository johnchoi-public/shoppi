import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { MeProvider, useMeQuery } from "./store";
import { Shell } from "./Shell";
import { Invite, Login, Signup } from "./pages/Auth";
import { BaysPage } from "./pages/Bays";
import { CalendarPage } from "./pages/Calendar";
import { CallsPage } from "./pages/Calls";
import { CustomerDetail, CustomersPage } from "./pages/Customers";
import { DocumentsPage } from "./pages/Documents";
import { HistoryPage } from "./pages/History";
import { IntakeQueue, IntakeWizard } from "./pages/Intake";
import { InventoryPage } from "./pages/Inventory";
import { PublicIntake } from "./pages/PublicIntake";
import { RequestsPage } from "./pages/Requests";
import { SettingsPage } from "./pages/Settings";
import { TeamPage } from "./pages/Team";
import { TodayPage } from "./pages/Today";
import { WorkOrderDetail, WorkOrdersPage } from "./pages/WorkOrders";
import { Component, type ReactNode } from "react";
import { Loading } from "./ui";

/** A crash in one screen shows a message instead of a blank page. */
class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-full items-center justify-center p-8">
        <div className="card max-w-md p-6 text-center">
          <h1 className="text-lg font-semibold text-slate-900">Something went wrong on this screen</h1>
          <p className="mt-2 text-sm text-slate-600">Your data is safe. Reload the page, or go back and try again.</p>
          <button className="mt-4 rounded-md bg-brand-700 px-3 py-1.5 text-sm font-medium text-white" onClick={() => location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}

export function App() {
  const location = useLocation();
  // The customer intake form is a separate, login-free flow with its own layout.
  if (location.pathname.startsWith("/i/")) {
    return <ErrorBoundary resetKey={location.pathname}><Routes><Route path="/i/:token" element={<PublicIntake />} /></Routes></ErrorBoundary>;
  }
  return <ErrorBoundary resetKey={location.pathname}><StaffApp /></ErrorBoundary>;
}

function StaffApp() {
  const me = useMeQuery();
  const qc = useQueryClient();
  const location = useLocation();

  useEffect(() => {
    const signedOut = () => qc.setQueryData(["me"], null);
    window.addEventListener("shoppi:signed-out", signedOut);
    return () => window.removeEventListener("shoppi:signed-out", signedOut);
  }, [qc]);

  if (me.isLoading) return <Loading />;
  if (!me.data) {
    return (
      <Routes>
        <Route path="/signup" element={<Signup />} />
        <Route path="/invite/:token" element={<Invite />} />
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }
  if (["/login", "/signup"].includes(location.pathname)) return <Navigate to="/" replace />;

  return (
    <MeProvider me={me.data}>
      <Routes>
        <Route path="/invite/:token" element={<Invite />} />
        {/* The staff intake wizard is its own focused flow, outside the main shell. */}
        <Route path="/intake/new" element={<IntakeWizard />} />
        <Route element={<Shell />}>
          <Route path="/" element={<TodayPage />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/bays" element={<BaysPage />} />
          <Route path="/intake" element={<IntakeQueue />} />
          <Route path="/customers" element={<CustomersPage />} />
          <Route path="/customers/:id" element={<CustomerDetail />} />
          <Route path="/work-orders" element={<WorkOrdersPage />} />
          <Route path="/work-orders/:id" element={<WorkOrderDetail />} />
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/team" element={<TeamPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/calls" element={<CallsPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/requests" element={<RequestsPage />} />
          <Route path="/settings" element={<Navigate to="/settings/shop" replace />} />
          <Route path="/settings/:tab" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </MeProvider>
  );
}
