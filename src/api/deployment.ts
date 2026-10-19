// mgllm: whether this deployment is read-only (server/readOnly.js). The page asks once and hides the controls the
// server would refuse. Until it knows, or when it cannot ask, every control shows, as in her own deployments.
import { useSyncExternalStore } from "react";
import { authHeaders } from "./authToken";
import { withBase } from "./base";

export interface Deployment {
  readOnly: boolean;
  /** The writes a read-only deployment still accepts, as "METHOD /path". */
  writable: string[];
}

const OPEN: Deployment = { readOnly: false, writable: [] };
let current: Deployment = OPEN;
let asked: Promise<Deployment> | null = null;
const listeners = new Set<() => void>();

export function loadDeployment(fetchImpl: typeof fetch = fetch): Promise<Deployment> {
  asked ??= fetchImpl(withBase("/api/deployment"), { headers: authHeaders() })
    .then((res) => (res.ok ? res.json() : null))
    .then((body) => {
      if (body && typeof body === "object") {
        current = {
          readOnly: body.readOnly === true,
          writable: Array.isArray(body.writable) ? body.writable.map(String) : [],
        };
        listeners.forEach((listener) => listener());
      }
      return current;
    })
    .catch(() => current);
  return asked;
}

export function isReadOnly(): boolean {
  return current.readOnly;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  void loadDeployment();
  return () => {
    listeners.delete(listener);
  };
}

/** True when the server refuses writes outside its allowlist: hide the controls that send them. */
export function useReadOnly(): boolean {
  return useSyncExternalStore(subscribe, isReadOnly, isReadOnly);
}

/** Whether the server accepts this write: always where it is not read-only, else only the writes it lists. */
export function canWrite(method: string, path: string): boolean {
  return !current.readOnly || current.writable.includes(`${method.toUpperCase()} ${path}`);
}

/** canWrite as a hook: a control that sends this write hides once the server says it would refuse it. */
export function useCanWrite(method: string, path: string): boolean {
  const get = () => canWrite(method, path);
  return useSyncExternalStore(subscribe, get, get);
}

export function resetDeploymentForTests(): void {
  current = OPEN;
  asked = null;
}
