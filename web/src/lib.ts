// Date helpers.
//
// The API speaks shop-local wall-clock times with no offset ("2026-11-06T15:00:00").
// We keep them in Date objects purely as wall-clock containers (built and read with
// local getters), and never call toISOString(), so the browser's own timezone
// never leaks in, even if the browser is in a different zone from the shop.

export const pad = (n: number) => String(n).padStart(2, "0");

export function parse(s: string): Date {
  const [d, t = "00:00:00"] = s.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [h, min, sec = 0] = t.split(":").map(Number);
  return new Date(y, m - 1, day, h, min, sec);
}

export const isoDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const iso = (d: Date) => `${isoDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;

/** "Now" on the shop's wall clock. */
export function shopNow(tz: string): Date {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const g = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return new Date(g("year"), g("month") - 1, g("day"), g("hour") % 24, g("minute"), g("second"));
}

export const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes());
export const addMinutes = (d: Date, n: number) => new Date(d.getTime() + n * 60000);
export const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export function startOfWeek(d: Date) {
  const s = startOfDay(d);
  return addDays(s, -((s.getDay() + 6) % 7)); // Monday
}
export const startOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1);
export const sameDay = (a: Date, b: Date) => isoDate(a) === isoDate(b);
export const minutesOfDay = (d: Date) => d.getHours() * 60 + d.getMinutes();

const asDate = (v: string | Date) => (typeof v === "string" ? parse(v) : v);

export function fmtTime(v: string | Date) {
  const d = asDate(v);
  const h = d.getHours();
  const m = d.getMinutes();
  return `${h % 12 || 12}${m ? ":" + pad(m) : ""}${h < 12 ? "am" : "pm"}`;
}
export const fmtDay = (v: string | Date, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }) =>
  asDate(v).toLocaleDateString("en-US", opts);
export const fmtDateTime = (v?: string | Date | null) => (v ? `${fmtDay(v)}, ${fmtTime(v)}` : "—");
export const fmtDate = (v?: string | null) => (v ? fmtDay(v.length <= 10 ? v + "T00:00:00" : v, { month: "short", day: "numeric", year: "numeric" }) : "—");
export const fmtRange = (a: string, b: string) => {
  const A = parse(a), B = parse(b);
  return sameDay(A, B) ? `${fmtDay(A)}, ${fmtTime(A)} – ${fmtTime(B)}` : `${fmtDay(A)}, ${fmtTime(A)} – ${fmtDay(B)}, ${fmtTime(B)}`;
};
export const fmtMoney = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
export const fmtMinutes = (m: number) => (m < 60 ? `${m} min` : `${Math.floor(m / 60)}h${m % 60 ? " " + (m % 60) + "m" : ""}`);

/** Value for <input type="datetime-local"> and back. */
export const toInput = (s?: string | null) => (s ? s.slice(0, 16) : "");
export const fromInput = (s: string) => (s ? (s.length === 16 ? s + ":00" : s) : null);

export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export const GVWR_CLASSES = [
  [1, "Class 1 · up to 6,000 lb (cars, small SUVs)"],
  [2, "Class 2 · 6,001–10,000 lb (full-size pickups, vans)"],
  [3, "Class 3 · 10,001–14,000 lb (heavy pickups)"],
  [4, "Class 4 · 14,001–16,000 lb"],
  [5, "Class 5 · 16,001–19,500 lb"],
  [6, "Class 6 · 19,501–26,000 lb (medium trucks)"],
  [7, "Class 7 · 26,001–33,000 lb"],
  [8, "Class 8 · over 33,000 lb (tractors)"],
] as const;
export const POWERTRAINS = [["gas", "Gasoline"], ["diesel", "Diesel"], ["hybrid", "Hybrid"], ["ev", "Electric"]] as const;
export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
