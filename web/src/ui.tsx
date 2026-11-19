// Shared UI building blocks. Plain Tailwind; no component library.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "./lib";

// ---- Buttons ----------------------------------------------------------------------

const VARIANTS = {
  primary: "bg-brand-700 text-white hover:bg-brand-800 disabled:bg-brand-700/50",
  secondary: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:text-slate-400",
  danger: "border border-red-200 bg-white text-red-700 hover:bg-red-50 disabled:text-red-300",
  ghost: "text-slate-600 hover:bg-slate-100 disabled:text-slate-300",
};

export function Button({ variant = "secondary", size = "md", className, busy, children, ...rest }:
  { variant?: keyof typeof VARIANTS; size?: "sm" | "md"; busy?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors",
        size === "sm" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm", VARIANTS[variant], className)}
    >
      {busy && <Spinner small />}
      {children}
    </button>
  );
}

export function Spinner({ small }: { small?: boolean }) {
  return <span className={cx("inline-block animate-spin rounded-full border-2 border-current border-t-transparent", small ? "h-3 w-3" : "h-5 w-5 text-slate-400")} role="status" aria-label="Loading" />;
}

export const Loading = () => <div className="flex justify-center p-10"><Spinner /></div>;

// ---- Form fields ------------------------------------------------------------------

export function Field({ label, hint, error, children, className }: { label?: string; hint?: string; error?: string; children: ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      {label && <span className="label">{label}</span>}
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-red-600">{error}</span>}
    </label>
  );
}

export const Input = (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={cx("input", p.className)} />;
export const Textarea = (p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea rows={3} {...p} className={cx("input", p.className)} />;
export function Select({ options, placeholder, ...p }: { options: readonly (readonly [string | number, string])[]; placeholder?: string } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...p} className={cx("input", p.className)}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );
}
export function Checkbox({ label, ...p }: { label: ReactNode } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="inline-flex items-center gap-2 text-sm">
      <input type="checkbox" {...p} className="h-4 w-4 rounded border-slate-300 text-brand-700 accent-teal-700" />
      <span>{label}</span>
    </label>
  );
}

/** Small state helper for forms: const [f, set, setAll] = useForm({name: ""}) */
export function useForm<T extends Record<string, any>>(initial: T) {
  const [values, setValues] = useState<T>(initial);
  const set = (key: keyof T) => (e: any) => {
    const t = e?.target;
    const value = t ? (t.type === "checkbox" ? t.checked : t.value) : e;
    setValues((v) => ({ ...v, [key]: value }));
  };
  return [values, set, setValues] as const;
}

// ---- Layout pieces ----------------------------------------------------------------

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className, pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={cx("card", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  slate: "bg-slate-100 text-slate-700", green: "bg-emerald-100 text-emerald-800", amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-800", blue: "bg-sky-100 text-sky-800", teal: "bg-brand-100 text-brand-800", violet: "bg-violet-100 text-violet-800",
};
export function Badge({ tone = "slate", children, title }: { tone?: keyof typeof TONES | string; children: ReactNode; title?: string }) {
  return <span title={title} className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone] ?? TONES.slate)}>{children}</span>;
}

const STATUS_TONE: Record<string, string> = {
  draft: "slate", scheduled: "blue", in_progress: "teal", waiting_parts: "amber", completed: "green", cancelled: "slate",
  planned: "blue", paused: "amber", done: "green", needs_reassignment: "red", pending: "amber", applied: "green", approved: "green",
  rejected: "red", denied: "red", stale: "slate", accepted: "green", ordered: "blue", received: "green", indexed: "green", failed: "red",
  valid: "green", expiring: "amber", expired: "red", active: "green", disabled: "slate", unassigned: "amber", processed: "green", new: "blue",
};
export const StatusBadge = ({ status }: { status: string }) => <Badge tone={STATUS_TONE[status] ?? "slate"}>{status.replace(/_/g, " ")}</Badge>;

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <p className="font-medium text-slate-700">{title}</p>
      {children && <p className="max-w-md text-sm text-slate-500">{children}</p>}
      {action}
    </div>
  );
}

export function Notice({ tone = "amber", children, className }: { tone?: "amber" | "red" | "blue" | "green"; children: ReactNode; className?: string }) {
  const tones = { amber: "border-amber-200 bg-amber-50 text-amber-900", red: "border-red-200 bg-red-50 text-red-900",
    blue: "border-sky-200 bg-sky-50 text-sky-900", green: "border-emerald-200 bg-emerald-50 text-emerald-900" };
  return <div className={cx("rounded-md border px-3 py-2 text-sm", tones[tone], className)}>{children}</div>;
}

export function Tabs({ tabs, value, onChange }: { tabs: readonly (readonly [string, ReactNode])[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="mb-4 flex gap-1 border-b border-slate-200" role="tablist">
      {tabs.map(([key, label]) => (
        <button key={key} role="tab" aria-selected={value === key} onClick={() => onChange(key)}
          className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", value === key ? "border-brand-700 text-brand-800" : "border-transparent text-slate-500 hover:text-slate-800")}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50"><tr>{head.map((h, i) => <th key={i} className="th">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
      {empty}
    </div>
  );
}

// ---- Overlays ---------------------------------------------------------------------

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEscape(onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[8vh]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" className={cx("fade-in w-full rounded-lg bg-white shadow-xl", wide ? "max-w-3xl" : "max-w-lg")}>
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="font-semibold text-slate-900">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto p-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-slate-100 px-4 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children, footer }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEscape(onClose);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside role="dialog" aria-modal="true" className="fade-in flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="font-semibold text-slate-900">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
        </header>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-slate-100 px-4 py-3">{footer}</footer>}
      </aside>
    </div>
  );
}

// ---- Toasts -----------------------------------------------------------------------

type Toast = { id: number; tone: "ok" | "error" | "info"; text: string };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const push = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = next.current++;
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 4000);
  }, []);
  const tones = { ok: "bg-slate-900 text-white", error: "bg-red-700 text-white", info: "bg-sky-700 text-white" };
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 left-1/2 z-[60] flex -translate-x-1/2 flex-col items-center gap-2" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={cx("fade-in pointer-events-auto max-w-lg rounded-md px-4 py-2 text-sm shadow-lg", tones[t.tone])}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

// ---- Before/after diff (history and change requests) ------------------------------

const HIDDEN = new Set(["id", "shop_id", "version", "created_at", "updated_at", "series_id", "sha256", "fingerprint"]);
// Internal record ids mean nothing to a reader; the row's label already says what it is.
const hidden = (k: string) => HIDDEN.has(k) || k.endsWith("_id") || k.endsWith("_by");
const ISO_TIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d/;
function show(v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && ISO_TIME.test(v)) {
    const d = new Date(Number(v.slice(0, 4)), Number(v.slice(5, 7)) - 1, Number(v.slice(8, 10)), Number(v.slice(11, 13)), Number(v.slice(14, 16)));
    return d.toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ") : "—";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function Diff({ op, before, after, changed }: { op: string; before?: any; after?: any; changed?: string[] | null }) {
  const source = (op === "insert" ? after : before) ?? {};
  const keys = (op === "update" ? (changed ?? []) : Object.keys(source).filter((k) => source[k] != null && source[k] !== "" && source[k] !== false)).filter((k) => !hidden(k));
  if (!keys.length) return <span className="text-xs text-slate-400">{op === "update" ? "Reassigned" : "No details"}</span>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {keys.slice(0, 14).map((k) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k.replace(/_/g, " ")}</dt>
          <dd className="min-w-0 break-words">
            {op === "update" && <><span className="text-red-700 line-through decoration-red-300">{show(before?.[k])}</span><span className="mx-1 text-slate-400">→</span></>}
            <span className={op === "delete" ? "text-red-700 line-through" : "text-emerald-800"}>{show(op === "delete" ? before?.[k] : after?.[k])}</span>
          </dd>
        </div>
      ))}
      {keys.length > 14 && <div className="col-span-2 text-slate-400">+ {keys.length - 14} more fields</div>}
    </dl>
  );
}
