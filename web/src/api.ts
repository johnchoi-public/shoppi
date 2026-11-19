// Thin client for the JSON API. Screens only ever call q() (read) and cmd() (write).

export class ApiError extends Error {
  status: number;
  code: string;
  extra: Record<string, any>;
  constructor(status: number, code: string, message: string, extra: Record<string, any> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

// A cross-site form can't set this header, which is what the server checks.
const HEADERS = { "X-Requested-With": "shoppi" };

async function parse(res: Response) {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) {
    const e = body?.error ?? {};
    const { code = "error", message = `Request failed (${res.status})`, ...extra } = e;
    if (res.status === 401 && !location.pathname.startsWith("/login") && !location.pathname.startsWith("/i/")) {
      window.dispatchEvent(new Event("shoppi:signed-out"));
    }
    throw new ApiError(res.status, code, message, extra);
  }
  return body;
}

export async function get(path: string) {
  return parse(await fetch(path, { credentials: "same-origin", headers: HEADERS }));
}

export async function post(path: string, body: any = {}) {
  return parse(
    await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { ...HEADERS, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function upload(path: string, form: FormData) {
  return parse(await fetch(path, { method: "POST", credentials: "same-origin", headers: HEADERS, body: form }));
}

/** Run a query. */
export const q = (name: string, args: any = {}) => post(`/api/q/${name}`, clean(args)).then((r) => r.data);

/** Run a command. Resolves to {status: "applied", result} or {status: "pending", change_request}. */
export const cmd = (name: string, args: any = {}) => post(`/api/cmd/${name}`, args);

function clean(args: any) {
  const out: any = {};
  for (const [k, v] of Object.entries(args ?? {})) if (v !== undefined && v !== "") out[k] = v;
  return out;
}

/** Read a Server-Sent Events response from POST /api/chat. */
export async function streamChat(body: any, onEvent: (e: any) => void, signal?: AbortSignal) {
  const res = await fetch("/api/chat", {
    method: "POST",
    credentials: "same-origin",
    headers: { ...HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) await parse(res);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let i;
    while ((i = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, i);
      buffer = buffer.slice(i + 2);
      if (chunk.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(chunk.slice(6)));
        } catch {
          /* ignore a malformed event */
        }
      }
    }
  }
}
