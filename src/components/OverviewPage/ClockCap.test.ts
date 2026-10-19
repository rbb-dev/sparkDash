// mgllm: the GPU clock control's selection and labels (ClockCap.tsx)
import { describe, expect, it } from "vitest";
import { clockLabel, clockSelection, type ClockUnit } from "./ClockCap";

const unit = (limit: string | null, ok = true): ClockUnit => ({ id: "x", name: "x", limit, ok, error: ok ? null : "down" });

describe("clockSelection", () => {
  it("is the one limit every unit reports", () => {
    expect(clockSelection([unit("2200"), unit("2200")])).toBe("2200");
    expect(clockSelection([unit("unlocked"), unit("unlocked")])).toBe("unlocked");
  });
  it("is mixed when the units differ, so the pair is never shown as one when it is not", () => {
    expect(clockSelection([unit("2200"), unit("unlocked")])).toBe("mixed");
  });
  it("ignores a unit that did not answer, and is null when none did", () => {
    expect(clockSelection([unit("2000"), unit(null, false)])).toBe("2000");
    expect(clockSelection([unit(null, false)])).toBeNull();
  });
});

describe("clockLabel", () => {
  it("reads as a pill", () => {
    expect(clockLabel("unlocked")).toBe("GPU auto");
    expect(clockLabel("2200")).toBe("GPU ≤ 2200");
    expect(clockLabel("mixed")).toBe("GPU mixed");
  });
});
