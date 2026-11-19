import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { get, post, startSession } from "../api";
import { Button, Checkbox, Field, Input, Loading, Notice, useForm } from "../ui";

function AuthLayout({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-slate-100 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex items-center justify-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-700 text-lg font-bold text-white">S</span>
          <span className="text-xl font-semibold text-slate-900">Shoppi</span>
        </div>
        <div className="card p-6">
          <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-4">{children}</div>
        </div>
      </div>
    </div>
  );
}

function useSubmit(action: () => Promise<any>, after: string = "/") {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await action();
      // Confirms the browser is really signed in (and falls back if it dropped the cookie),
      // so a sign-in can never "succeed" and leave the form sitting there.
      await startSession(res?.token);
      await qc.invalidateQueries({ queryKey: ["me"] });
      nav(after);
    } catch (err: any) {
      setError(err?.message ?? "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }
  return { submit, error, busy };
}

function GoogleButton() {
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => get("/api/auth/config"), staleTime: Infinity });
  if (!config.data?.google) return null;
  return (
    <>
      <div className="my-4 flex items-center gap-3 text-xs text-slate-400"><span className="h-px flex-1 bg-slate-200" />or<span className="h-px flex-1 bg-slate-200" /></div>
      <a href="/api/auth/google/start" className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">
        Sign in with Google
      </a>
    </>
  );
}

export function Login() {
  const [f, set] = useForm({ email: "", password: "" });
  const [params] = useSearchParams();
  const { submit, error, busy } = useSubmit(() => post("/api/auth/login", f));
  return (
    <AuthLayout title="Sign in" subtitle="Manage your shop's schedule, customers and parts.">
      <form onSubmit={submit} className="space-y-3">
        {(error || params.get("error")) && <Notice tone="red">{error || params.get("error")}</Notice>}
        <Field label="Email"><Input type="email" autoComplete="email" required autoFocus value={f.email} onChange={set("email")} /></Field>
        <Field label="Password"><Input type="password" autoComplete="current-password" required value={f.password} onChange={set("password")} /></Field>
        <Button type="submit" variant="primary" className="w-full" busy={busy}>Sign in</Button>
      </form>
      <GoogleButton />
      <p className="mt-4 text-center text-sm text-slate-500">New shop? <Link className="link" to="/signup">Create an account</Link></p>
    </AuthLayout>
  );
}

const TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu", "America/Toronto", "America/Vancouver", "Europe/London"];

export function Signup() {
  const guess = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [f, set] = useForm({ shop_name: "", name: "", email: "", password: "", timezone: guess || "America/New_York", starter: true });
  const { submit, error, busy } = useSubmit(() => post("/api/auth/signup", f));
  const zones = TIMEZONES.includes(f.timezone) ? TIMEZONES : [f.timezone, ...TIMEZONES];
  return (
    <AuthLayout title="Set up your shop" subtitle="You'll be the owner. You can invite your team afterwards.">
      <form onSubmit={submit} className="space-y-3">
        {error && <Notice tone="red">{error}</Notice>}
        <Field label="Shop name"><Input required autoFocus value={f.shop_name} onChange={set("shop_name")} placeholder="e.g. Northside Auto & Truck" /></Field>
        <Field label="Your name"><Input required autoComplete="name" value={f.name} onChange={set("name")} /></Field>
        <Field label="Email"><Input type="email" required autoComplete="email" value={f.email} onChange={set("email")} /></Field>
        <Field label="Password" hint="At least 8 characters."><Input type="password" required minLength={8} autoComplete="new-password" value={f.password} onChange={set("password")} /></Field>
        <Field label="Shop timezone" hint="All times in the app are shown in this timezone.">
          <select className="input" value={f.timezone} onChange={set("timezone")}>{zones.map((z) => <option key={z}>{z}</option>)}</select>
        </Field>
        <Checkbox label="Start with typical bays and a service catalog (you can edit them)" checked={f.starter} onChange={set("starter")} />
        <Button type="submit" variant="primary" className="w-full" busy={busy}>Create shop</Button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500">Already have an account? <Link className="link" to="/login">Sign in</Link></p>
    </AuthLayout>
  );
}

export function Invite() {
  const { token = "" } = useParams();
  const info = useQuery({ queryKey: ["invite", token], queryFn: () => get(`/api/auth/invite/${token}`), retry: false });
  const [f, set] = useForm({ name: "", password: "" });
  const { submit, error, busy } = useSubmit(() => post(`/api/auth/invite/${token}`, f));
  if (info.isLoading) return <Loading />;
  if (info.isError) {
    return <AuthLayout title="Invite not found"><Notice tone="red">{(info.error as any)?.message}</Notice><p className="mt-4 text-sm"><Link className="link" to="/login">Go to sign in</Link></p></AuthLayout>;
  }
  const d = info.data;
  return (
    <AuthLayout title={`Join ${d.shop_name}`} subtitle={<>You've been invited as <b className="capitalize">{d.role}</b> ({d.email}).</>}>
      <form onSubmit={submit} className="space-y-3">
        {error && <Notice tone="red">{error}</Notice>}
        {!d.has_account && <Field label="Your name"><Input required autoFocus value={f.name} onChange={set("name")} /></Field>}
        <Field label={d.has_account ? "Your Shoppi password" : "Choose a password"} hint={d.has_account ? "You already have an account with this email." : "At least 8 characters."}>
          <Input type="password" required minLength={8} value={f.password} onChange={set("password")} />
        </Field>
        <Button type="submit" variant="primary" className="w-full" busy={busy}>Join shop</Button>
      </form>
    </AuthLayout>
  );
}
