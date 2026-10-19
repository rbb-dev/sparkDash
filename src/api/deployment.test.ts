// mgllm: the page asks the server once whether it is read-only (server/readOnly.js)
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { canWrite, isReadOnly, loadDeployment, resetDeploymentForTests, useReadOnly } from "./deployment";
import { ClearMenu } from "../components/ui/ClearMenu";

const answer = (body: unknown, ok = true) =>
  vi.fn(async () => ({ ok, json: async () => body }) as unknown as Response);

beforeEach(() => resetDeploymentForTests());

describe("loadDeployment", () => {
  it("learns a read-only deployment and the writes it allows, asking once", async () => {
    const fetchImpl = answer({ readOnly: true, writable: ["PUT /api/settings"] });
    expect(await loadDeployment(fetchImpl)).toEqual({ readOnly: true, writable: ["PUT /api/settings"] });
    await loadDeployment(fetchImpl);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(isReadOnly()).toBe(true);
  });

  it("shows every control when the server says nothing, as her own deployments do", async () => {
    expect(await loadDeployment(answer({}, false))).toEqual({ readOnly: false, writable: [] });
    resetDeploymentForTests();
    const failing = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    expect((await loadDeployment(failing)).readOnly).toBe(false);
    expect(isReadOnly()).toBe(false);
  });

  it("reads only a literal true as read-only", async () => {
    expect((await loadDeployment(answer({ readOnly: "yes" }))).readOnly).toBe(false);
  });
});

describe("useReadOnly", () => {
  const menu = () =>
    renderToString(
      createElement(ClearMenu<undefined>, {
        label: "Clear history…",
        choices: [{ id: "all", label: "Everything", ask: "Delete everything?", arg: undefined }],
        onRun: async () => 0,
      })
    );

  it("hides a control that writes once the server says read-only, and shows it otherwise", async () => {
    expect(menu()).toContain("Clear history…");
    await loadDeployment(answer({ readOnly: true, writable: [] }));
    expect(menu()).toBe("");
    const Probe = () => createElement("i", null, String(useReadOnly()));
    expect(renderToString(createElement(Probe))).toContain("true");
  });
});

// mgllm (sparkDash 2.50): a read-only deployment may still allow a write it lists (PUT /api/settings here), so a control
// is hidden by the write it sends, not by read-only alone (her alerts dialog saves to PUT /api/alerts/config).
describe("canWrite", () => {
  it("allows every write where the deployment is not read-only", async () => {
    await loadDeployment(answer({ readOnly: false, writable: [] }));
    expect(canWrite("PUT", "/api/alerts/config")).toBe(true);
  });

  it("in a read-only deployment allows exactly the writes it lists", async () => {
    await loadDeployment(answer({ readOnly: true, writable: ["PUT /api/settings"] }));
    expect(canWrite("PUT", "/api/settings")).toBe(true);
    expect(canWrite("put", "/api/settings")).toBe(true);
    expect(canWrite("PUT", "/api/alerts/config")).toBe(false);
    expect(canWrite("POST", "/api/settings")).toBe(false);
  });
});
