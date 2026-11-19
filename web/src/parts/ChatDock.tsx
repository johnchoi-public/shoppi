// The assistant. Streams the reply; anything the assistant wants to change shows
// up as a proposal card that the user confirms or declines.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { post, streamChat } from "../api";
import { cx } from "../lib";
import { Button, Diff, Spinner } from "../ui";

type Msg =
  | { kind: "user" | "assistant" | "error"; text: string }
  | { kind: "tool"; name: string }
  | { kind: "proposal"; cr: any; state: "pending" | "applied" | "declined" | "stale" | "error"; note?: string };

/** Models answer in light Markdown; render the bold markers instead of showing asterisks. Text only: no HTML is ever injected. */
function Rich({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => (part.startsWith("**") && part.endsWith("**") && part.length > 4 ? <strong key={i}>{part.slice(2, -2)}</strong> : part))}</>;
}

const SUGGESTIONS = ["What's on the schedule tomorrow?", "Which bays are free right now?", "Which parts are running low?", "Whose certifications expire soon?"];

export function ChatDock({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const thread = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  // Braces matter: scrollIntoView() returns a Promise in current browsers, and React would treat a returned value as a cleanup function.
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages]);
  useEffect(() => () => abort.current?.abort(), []);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { kind: "user", text }]);
    abort.current = new AbortController();
    try {
      await streamChat({ message: text, thread_id: thread.current }, (e) => {
        if (e.type === "thread") thread.current = e.thread_id;
        else if (e.type === "token") {
          setMessages((m) => {
            const last = m[m.length - 1];
            if (last?.kind === "assistant") return [...m.slice(0, -1), { kind: "assistant", text: last.text + e.text }];
            return [...m, { kind: "assistant", text: e.text }];
          });
        } else if (e.type === "tool_call") {
          // Drop an empty bubble left by a model that emits whitespace before calling a tool.
          setMessages((m) => {
            const last = m[m.length - 1];
            const kept = last?.kind === "assistant" && !last.text.trim() ? m.slice(0, -1) : m;
            return [...kept, { kind: "tool", name: e.name }];
          });
        }
        else if (e.type === "proposal") setMessages((m) => [...m, { kind: "proposal", cr: e.change_request, state: "pending" }]);
        else if (e.type === "error") setMessages((m) => [...m, { kind: "error", text: e.message }]);
      }, abort.current.signal);
    } catch (e: any) {
      if (e?.name !== "AbortError") setMessages((m) => [...m, { kind: "error", text: e?.message ?? "The assistant is unavailable." }]);
    } finally {
      setBusy(false);
      qc.invalidateQueries({ queryKey: ["q"] });
    }
  }

  async function decide(index: number, approve: boolean) {
    const msg = messages[index];
    if (msg.kind !== "proposal") return;
    let state: any = approve ? "applied" : "declined";
    let note: string | undefined;
    try {
      const res = await post(`/api/change_requests/${msg.cr.id}/decide`, { approve });
      if (res.status === "stale") { state = "stale"; note = "The record changed in the meantime, so this was not applied."; }
    } catch (e: any) {
      state = "error";
      note = e?.message;
    }
    setMessages((m) => m.map((x, i) => (i === index ? { ...msg, state, note } : x)));
    qc.invalidateQueries({ queryKey: ["q"] });
  }

  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-slate-200 bg-white" aria-label="Assistant">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Assistant</h2>
          <p className="text-[11px] text-slate-500">Changes it suggests need your confirmation.</p>
        </div>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => { thread.current = null; setMessages([]); }}>New chat</Button>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close assistant">✕</Button>
        </div>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-slate-500">Ask about the schedule, customers, bays, parts or your shop documents.</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => send(s)} className="block w-full rounded-md border border-slate-200 px-3 py-2 text-left text-sm text-slate-700 hover:border-brand-500 hover:bg-brand-50">{s}</button>
            ))}
          </div>
        )}
        {messages.map((m, i) => {
          if (m.kind === "tool") return <div key={i} className="text-xs text-slate-400">· looked up {m.name.replace(/_/g, " ")}</div>;
          if (m.kind === "proposal") {
            return (
              <div key={i} className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-800">Proposed change</div>
                <div className="mb-2 text-sm font-medium text-slate-800">{m.cr.summary || m.cr.command}</div>
                <div className="space-y-2 rounded bg-white/70 p-2">
                  {(m.cr.preview ?? []).slice(0, 4).map((p: any, j: number) => (
                    <div key={j}>
                      <div className="mb-0.5 text-[11px] font-medium text-slate-500">{p.op} {p.entity_type.replace(/_/g, " ")}</div>
                      <Diff op={p.op} before={p.before} after={p.after} changed={p.changed_fields} />
                    </div>
                  ))}
                  {!m.cr.preview?.length && <pre className="whitespace-pre-wrap text-xs text-slate-600">{JSON.stringify(m.cr.args, null, 1)}</pre>}
                </div>
                {m.state === "pending" ? (
                  m.cr.can_decide ? (
                    <div className="mt-2 flex gap-2">
                      <Button size="sm" variant="primary" onClick={() => decide(i, true)}>Confirm</Button>
                      <Button size="sm" onClick={() => decide(i, false)}>Decline</Button>
                    </div>
                  ) : <p className="mt-2 text-xs text-amber-900">Sent to an owner or advisor for approval.</p>
                ) : (
                  <p className={cx("mt-2 text-xs font-medium", m.state === "applied" ? "text-emerald-700" : "text-slate-600")}>
                    {m.state === "applied" ? "✓ Applied" : m.state === "declined" ? "Declined" : m.note}
                  </p>
                )}
              </div>
            );
          }
          if (!m.text.trim()) return null;
          return (
            <div key={i} className={cx("max-w-[92%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
              m.kind === "user" ? "ml-auto w-fit bg-brand-700 text-white" : m.kind === "error" ? "bg-red-50 text-red-800" : "bg-slate-100 text-slate-800")}>
              {m.kind === "assistant" ? <Rich text={m.text} /> : m.text}
            </div>
          );
        })}
        {busy && <div className="flex items-center gap-2 text-xs text-slate-400"><Spinner small /> Thinking…</div>}
        <div ref={bottom} />
      </div>

      <form onSubmit={(e: FormEvent) => { e.preventDefault(); send(input); }} className="flex gap-2 border-t border-slate-100 p-3">
        <input className="input" placeholder="Ask the assistant…" value={input} onChange={(e) => setInput(e.target.value)} aria-label="Message" />
        <Button type="submit" variant="primary" disabled={!input.trim()} busy={busy}>Send</Button>
      </form>
    </aside>
  );
}
