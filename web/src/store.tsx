// App-wide state: who is signed in, what they may do, and helpers to read/write data.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, type ReactNode } from "react";
import { ApiError, cmd, get, q } from "./api";
import { shopNow } from "./lib";
import { useToast } from "./ui";

export type Me = {
  user: { id: string; name: string; role: string; employee_id: string | null; is_manager: boolean };
  shop: { id: string; name: string; timezone: string; hours: Record<string, string[] | null> };
  permissions: Record<string, "none" | "view" | "edit">;
  verticals: Record<string, string>;
  grantable: string[];
  shops: { shop_id: string; shop_name: string; role: string }[];
};

const MeCtx = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeCtx);
  if (!me) throw new Error("not signed in");
  return me;
}
export const MeProvider = ({ me, children }: { me: Me; children: ReactNode }) => <MeCtx.Provider value={me}>{children}</MeCtx.Provider>;

export function useMeQuery() {
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: () => get("/api/auth/me").catch((e) => { if (e instanceof ApiError && e.status === 401) return null; throw e; }),
    retry: false,
    staleTime: 30_000,
  });
}

/** Can the signed-in user edit (or at least view) an area? */
export function useCan() {
  const me = useMe();
  return {
    edit: (v: string) => me.permissions[v] === "edit",
    view: (v: string) => me.permissions[v] !== "none" && me.permissions[v] !== undefined,
    manager: me.user.is_manager,
  };
}

export const useNow = () => shopNow(useMe().shop.timezone);

/** Read data. Re-fetched automatically after any command succeeds. */
export function useQ<T = any>(name: string, args: any = {}, opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  return useQuery<T>({ queryKey: ["q", name, args], queryFn: () => q(name, args), ...opts });
}

type RunOpts = { ok?: string; quiet?: boolean; rethrow?: boolean };

/**
 * Run a command and handle the three outcomes the same way everywhere:
 *  - applied  -> refresh data, show `ok`, resolve to the result
 *  - pending  -> the change was sent for approval (the user can only view this area); resolves to null
 *  - error    -> show the message; resolves to undefined
 * So `r !== undefined` means "it went through, close the dialog".
 */
export function useRun() {
  const qc = useQueryClient();
  const toast = useToast();
  return useCallback(async (name: string, args: any = {}, opts: RunOpts = {}) => {
    try {
      const res = await cmd(name, args);
      qc.invalidateQueries({ queryKey: ["q"] });
      if (res.status === "pending") {
        toast(res.change_request?.needs === "self" ? "Saved as a proposal for you to confirm." : "Sent to an owner or advisor for approval.", "info");
        return null;
      }
      if (opts.ok && !opts.quiet) toast(opts.ok);
      return res.result ?? true;
    } catch (e: any) {
      if (opts.rethrow) throw e;
      toast(e?.message ?? "Something went wrong.", "error");
      return undefined;
    }
  }, [qc, toast]);
}

export function useRefresh() {
  const qc = useQueryClient();
  return useCallback(() => qc.invalidateQueries(), [qc]);
}
