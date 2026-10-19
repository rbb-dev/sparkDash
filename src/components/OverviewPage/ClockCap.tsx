// mgllm: the GPU clock control beside the head's role pill (server/gpuClock.js). One limit for every unit read through
// sparkdash-api: a tensor-parallel pair runs at its slower GPU's pace, so its GPUs are never set apart.
import { useCallback, useEffect, useState } from "react";
import { authHeaders } from "../../api/authToken";
import { withBase } from "../../api/base";

export interface ClockUnit {
  id: string;
  name: string;
  limit: string | null;
  ok: boolean;
  error: string | null;
}

interface ClockState {
  available: boolean;
  limits: string[];
  units: ClockUnit[];
}

/** The limit every answering unit reports, "mixed" when they differ, null when none answered. */
export function clockSelection(units: readonly ClockUnit[]): string | null {
  const limits = new Set(units.filter((u) => u.ok && u.limit).map((u) => u.limit as string));
  if (limits.size === 0) return null;
  return limits.size === 1 ? [...limits][0] : "mixed";
}

export function clockLabel(limit: string): string {
  if (limit === "unlocked") return "GPU auto";
  if (limit === "mixed") return "GPU mixed";
  return `GPU ≤ ${limit}`;
}

async function call(opts?: RequestInit): Promise<ClockState> {
  const res = await fetch(withBase("/api/gpu-clock"), {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeaders() },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok && !body.units) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

export function ClockCap() {
  const [state, setState] = useState<ClockState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    call()
      .then((s) => {
        setState(s);
        setError(s.units.find((u) => !u.ok)?.error ?? null);
      })
      .catch((e) => setError(String(e?.message || e)));
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 60_000);
    return () => window.clearInterval(t);
  }, [load]);

  if (!state?.available) return null;
  const selected = clockSelection(state.units);
  const names = state.units.map((u) => u.name).join(" + ");

  const change = async (limit: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await call({ method: "POST", body: JSON.stringify({ limit }) });
      const failed = (res.units || []).filter((u) => !u.ok);
      if (failed.length) setError(failed.map((u) => `${u.name}: ${u.error}`).join("; "));
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setBusy(false);
      load();
    }
  };

  return (
    <select
      className={`tag ov-clockcap${error ? " tag--warn" : ""}`}
      value={selected ?? ""}
      disabled={busy}
      aria-label="GPU clock cap"
      title={error ? `GPU clock cap: ${error}` : `GPU clock cap for ${names}, all together (a tensor-parallel pair runs at its slower GPU's pace)`}
      onChange={(e) => void change(e.target.value)}
    >
      {selected === null || selected === "mixed" ? (
        <option value={selected ?? ""} disabled>
          {selected === null ? "GPU …" : clockLabel("mixed")}
        </option>
      ) : null}
      {state.limits.map((l) => (
        <option key={l} value={l}>
          {clockLabel(l)}
        </option>
      ))}
    </select>
  );
}
