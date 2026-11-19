// The customer-facing intake form (kiosk tablet or a link sent to the customer).
// No login, no shop navigation, and it can read nothing about the shop except its
// name. What is submitted waits for staff review before anything is created.
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { get, post } from "../api";
import { cx, POWERTRAINS } from "../lib";
import { Button, Loading } from "../ui";

const STEPS = ["About you", "Your vehicle", "What's going on", "Review"];
const big = "block w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-base shadow-xs focus:border-brand-500 focus:ring-2 focus:ring-brand-200 focus:outline-none";

export function PublicIntake() {
  const { token = "" } = useParams();
  const info = useQuery({ queryKey: ["public-intake", token], queryFn: () => get(`/api/public/intake/${token}`), retry: false });
  const [step, setStep] = useState(0);
  const [c, setC] = useState({ name: "", phone: "", email: "", preferred_contact: "" });
  const [v, setV] = useState({ vin: "", plate: "", year: "", make: "", model: "", powertrain: "", mileage: "" });
  const [concern, setConcern] = useState("");
  const [times, setTimes] = useState("");
  const [dropOff, setDropOff] = useState<boolean | null>(null);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [decoding, setDecoding] = useState(false);
  const [vinNote, setVinNote] = useState("");

  if (info.isLoading) return <Loading />;
  if (info.isError) {
    return <Frame shop="Vehicle service"><div className="rounded-xl bg-white p-8 text-center shadow"><h1 className="text-xl font-semibold">This link is no longer active</h1><p className="mt-2 text-slate-600">Please ask the shop for a new link, or give them a call.</p></div></Frame>;
  }
  const shop = info.data;

  async function decode() {
    setDecoding(true);
    setVinNote("");
    try {
      const d = await get(`/api/public/intake/${token}/vin/${encodeURIComponent(v.vin.trim())}`);
      setV((x) => ({ ...x, year: d.year ? String(d.year) : x.year, make: d.make ?? x.make, model: d.model ?? x.model, powertrain: d.powertrain ?? x.powertrain }));
      setVinNote(`Found: ${[d.year, d.make, d.model].filter(Boolean).join(" ")}`);
    } catch (e: any) {
      setVinNote(e?.message ?? "We couldn't look that up. Please fill in the details below.");
    } finally {
      setDecoding(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const res = await post(`/api/public/intake/${token}`, {
        customer: { name: c.name, phone: c.phone || null, email: c.email || null, preferred_contact: c.preferred_contact || null },
        vehicle: { vin: v.vin || null, plate: v.plate || null, year: v.year ? Number(v.year) : null, make: v.make || null, model: v.model || null,
          powertrain: v.powertrain || null, mileage: v.mileage ? Number(v.mileage) : null },
        concern, preferred_times: times || null, drop_off: dropOff, consent,
      });
      setDone(res.reference);
    } catch (e: any) {
      setError(e?.message ?? "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setStep(0); setC({ name: "", phone: "", email: "", preferred_contact: "" }); setV({ vin: "", plate: "", year: "", make: "", model: "", powertrain: "", mileage: "" });
    setConcern(""); setTimes(""); setDropOff(null); setConsent(false); setDone(null); setVinNote("");
  }

  if (done) {
    return (
      <Frame shop={shop.shop_name}>
        <div className="fade-in rounded-xl bg-white p-8 text-center shadow">
          <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-2xl text-emerald-700">✓</div>
          <h1 className="text-2xl font-semibold text-slate-900">Thank you, {c.name.split(" ")[0]}!</h1>
          <p className="mt-2 text-slate-600">{shop.shop_name} has your details and will contact you to confirm a time.</p>
          <p className="mt-1 text-sm text-slate-400">Reference {done}</p>
          <Button className="mt-6" onClick={reset}>Start a new form</Button>
        </div>
      </Frame>
    );
  }

  const ok = [c.name.trim() && (c.phone.trim() || c.email.trim()), v.make || v.model || v.vin || v.plate, concern.trim(), consent][step];
  return (
    <Frame shop={shop.shop_name}>
      <ol className="mb-4 flex gap-1.5" aria-label="Progress">
        {STEPS.map((s, i) => <li key={s} className={cx("h-2 flex-1 rounded-full", i <= step ? "bg-brand-600" : "bg-slate-300")} aria-current={i === step} />)}
      </ol>
      <div className="fade-in rounded-xl bg-white p-6 shadow sm:p-8" key={step}>
        <p className="text-sm font-medium text-brand-700">Step {step + 1} of {STEPS.length}</p>
        <h1 className="mb-5 text-2xl font-semibold text-slate-900">{STEPS[step]}</h1>
        {error && <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-red-800">{error}</div>}

        {step === 0 && (
          <div className="space-y-4">
            <Big label="Your name"><input className={big} autoFocus autoComplete="name" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} /></Big>
            <Big label="Mobile number"><input className={big} type="tel" inputMode="tel" autoComplete="tel" value={c.phone} onChange={(e) => setC({ ...c, phone: e.target.value })} /></Big>
            <Big label="Email (optional if you gave a phone number)"><input className={big} type="email" autoComplete="email" value={c.email} onChange={(e) => setC({ ...c, email: e.target.value })} /></Big>
            <Big label="How should we contact you?">
              <Choices value={c.preferred_contact} onChange={(x) => setC({ ...c, preferred_contact: x })} options={[["phone", "Call"], ["text", "Text"], ["email", "Email"]]} />
            </Big>
          </div>
        )}

        {step === 1 && (
          <div className="space-y-4">
            <Big label="VIN (optional)" hint="On the driver's door jamb or your registration. We'll fill in the rest for you.">
              <div className="flex gap-2">
                <input className={big} value={v.vin} maxLength={17} onChange={(e) => setV({ ...v, vin: e.target.value.toUpperCase() })} />
                <button type="button" onClick={decode} disabled={v.vin.trim().length !== 17 || decoding} className="rounded-lg bg-slate-800 px-4 font-medium whitespace-nowrap text-white disabled:bg-slate-300">{decoding ? "…" : "Look up"}</button>
              </div>
              {vinNote && <p className="mt-1 text-sm text-slate-600">{vinNote}</p>}
            </Big>
            <div className="grid grid-cols-3 gap-3">
              <Big label="Year"><input className={big} inputMode="numeric" value={v.year} onChange={(e) => setV({ ...v, year: e.target.value.replace(/\D/g, "").slice(0, 4) })} /></Big>
              <Big label="Make"><input className={big} value={v.make} onChange={(e) => setV({ ...v, make: e.target.value })} /></Big>
              <Big label="Model"><input className={big} value={v.model} onChange={(e) => setV({ ...v, model: e.target.value })} /></Big>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Big label="Licence plate"><input className={big} value={v.plate} onChange={(e) => setV({ ...v, plate: e.target.value.toUpperCase() })} /></Big>
              <Big label="Mileage"><input className={big} inputMode="numeric" value={v.mileage} onChange={(e) => setV({ ...v, mileage: e.target.value.replace(/\D/g, "") })} /></Big>
            </div>
            <Big label="Fuel type"><Choices value={v.powertrain} onChange={(x) => setV({ ...v, powertrain: x })} options={POWERTRAINS as any} /></Big>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <Big label="What would you like us to look at?" hint="Describe the problem or the service you need, in your own words.">
              <textarea className={big} rows={5} autoFocus value={concern} maxLength={4000} onChange={(e) => setConcern(e.target.value)} />
            </Big>
            <Big label="When works best for you?"><input className={big} value={times} maxLength={500} onChange={(e) => setTimes(e.target.value)} placeholder="e.g. Any morning next week" /></Big>
            <Big label="Will you wait or drop the vehicle off?">
              <Choices value={dropOff === null ? "" : dropOff ? "drop" : "wait"} onChange={(x) => setDropOff(x === "" ? null : x === "drop")} options={[["wait", "I'll wait"], ["drop", "Drop off"]]} />
            </Big>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-4">
            <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200 text-base">
              <Row label="Name" value={c.name} />
              <Row label="Contact" value={[c.phone, c.email].filter(Boolean).join(" · ")} />
              <Row label="Vehicle" value={[v.year, v.make, v.model].filter(Boolean).join(" ") || v.plate || v.vin} />
              <Row label="Request" value={concern} />
              {times && <Row label="Preferred time" value={times} />}
            </dl>
            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-200 p-4">
              <input type="checkbox" className="mt-1 h-5 w-5 accent-teal-700" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span className="text-slate-700">{shop.shop_name} may contact me about this request using the details above.</span>
            </label>
          </div>
        )}

        <div className="mt-8 flex justify-between gap-3">
          {step > 0 ? <button onClick={() => setStep(step - 1)} className="rounded-lg border border-slate-300 px-5 py-3 text-base font-medium text-slate-700">Back</button> : <span />}
          {step < 3
            ? <button disabled={!ok} onClick={() => setStep(step + 1)} className="rounded-lg bg-brand-700 px-6 py-3 text-base font-semibold text-white disabled:bg-slate-300">Continue</button>
            : <button disabled={!ok || busy} onClick={submit} className="rounded-lg bg-brand-700 px-6 py-3 text-base font-semibold text-white disabled:bg-slate-300">{busy ? "Sending…" : "Send to the shop"}</button>}
        </div>
      </div>
    </Frame>
  );
}

function Frame({ shop, children }: { shop: string; children: ReactNode }) {
  return (
    <div className="min-h-full bg-slate-100 px-4 py-8">
      <div className="mx-auto max-w-xl">
        <header className="mb-5 text-center">
          <p className="text-sm font-medium uppercase tracking-wide text-slate-500">Welcome to</p>
          <p className="text-2xl font-bold text-slate-900">{shop}</p>
        </header>
        {children}
      </div>
    </div>
  );
}

const Big = ({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) => (
  <label className="block"><span className="mb-1.5 block text-base font-medium text-slate-700">{label}</span>{children}{hint && <span className="mt-1 block text-sm text-slate-500">{hint}</span>}</label>
);

function Choices({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(([k, label]) => (
        <button key={k} type="button" onClick={() => onChange(value === k ? "" : k)} aria-pressed={value === k}
          className={cx("rounded-lg border px-4 py-2.5 text-base font-medium", value === k ? "border-brand-600 bg-brand-50 text-brand-800" : "border-slate-300 bg-white text-slate-700")}>{label}</button>
      ))}
    </div>
  );
}

const Row = ({ label, value }: { label: string; value: string }) => (
  <div className="flex gap-4 px-4 py-3"><dt className="w-32 shrink-0 text-slate-500">{label}</dt><dd className="min-w-0 flex-1 whitespace-pre-wrap text-slate-900">{value || "—"}</dd></div>
);
