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
const BASE_HEADERS = { "X-Requested-With": "shoppi" };

// ---- Session -----------------------------------------------------------------------
// Normally the session lives in an HttpOnly cookie that scripts never see. Some
// browsers refuse to keep it (cookies blocked for the site, or the app embedded in
// another page). Sign-in then succeeds on the server but every later request is
// anonymous. When we detect that, we fall back to sending the session token in a
// header, kept for this tab only.
const TOKEN_KEY = "shoppi.session";
let tabToken: string | null = null;
try {
  tabToken = sessionStorage.getItem(TOKEN_KEY);
} catch {
  /* storage is blocked too: the token will live in memory until the page reloads */
}

function setTabToken(token: string | null) {
  tabToken = token;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* see above */
  }
}

const headers = (): Record<string, string> => (tabToken ? { ...BASE_HEADERS, Authorization: `Bearer ${tabToken}` } : BASE_HEADERS);

/** "cookie" = the normal session; "tab" = the browser dropped the cookie and we are using the per-tab fallback. */
export const sessionMode = (): "cookie" | "tab" => (tabToken ? "tab" : "cookie");

/**
 * Call after sign-in, sign-up, accepting an invite or switching shop, with that
 * response's token. Checks that the browser really is signed in, and switches to
 * the per-tab fallback if the cookie did not stick. Throws if neither works, so
 * the form can say so instead of silently doing nothing.
 */
export async function startSession(token?: string | null): Promise<"cookie" | "tab"> {
  const signedIn = async () => (await fetch("/api/auth/me", { credentials: "same-origin", headers: headers(), cache: "no-store" })).ok;
  setTabToken(null);
  if (await signedIn()) return "cookie";
  if (token) {
    setTabToken(token);
    if (await signedIn()) return "tab";
    setTabToken(null);
  }
  throw new ApiError(401, "session_not_kept", "You were signed in, but this browser did not keep the session. Allow cookies for this site and try again.");
}

export function endSession() {
  setTabToken(null);
}

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
    // A rejected fallback token must not shadow a cookie that may still be valid.
    if (res.status === 401 && tabToken) setTabToken(null);
    if (res.status === 401 && !location.pathname.startsWith("/login") && !location.pathname.startsWith("/i/")) {
      window.dispatchEvent(new Event("shoppi:signed-out"));
    }
    throw new ApiError(res.status, code, message, extra);
  }
  return body;
}

export async function get(path: string) {
  return parse(await fetch(path, { credentials: "same-origin", headers: headers() }));
}

export async function post(path: string, body: any = {}) {
  return parse(
    await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function upload(path: string, form: FormData) {
  return parse(await fetch(path, { method: "POST", credentials: "same-origin", headers: headers(), body: form }));
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
    headers: { ...headers(), "Content-Type": "application/json" },
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
