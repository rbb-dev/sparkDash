// mgllm: the page under a sub-path (VITE_BASE): every address it writes carries the base, every address it reads
// loses it first, so a reload or a link opened in a new tab stays under the sub-path
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stripBase, withBase } from "./base";
import { ENERGY_ID, OVERVIEW_ID, SHOWCASE_ID, benchId, idToPath, initialActiveId, pathToId } from "../constants";
import { parsePath } from "../hooks/useRoute";
import { showcaseUrl } from "../components/shell/sparkSummary";
import type { SparkSnapshot } from "./types";

afterEach(() => vi.unstubAllEnvs());

describe("under /sparkdash/", () => {
  beforeEach(() => vi.stubEnv("BASE_URL", "/sparkdash/"));

  it("writes every page's address under the base", () => {
    expect(idToPath(OVERVIEW_ID)).toBe("/sparkdash/");
    expect(idToPath(null)).toBe("/sparkdash/");
    expect(idToPath("spark1")).toBe("/sparkdash/spark/spark1");
    expect(idToPath(ENERGY_ID)).toBe("/sparkdash/energy");
    expect(idToPath(benchId("decode"))).toBe("/sparkdash/bench/decode");
  });

  it("reads an address under the base as the page's own", () => {
    expect(pathToId("/sparkdash/spark/spark1")).toBe("spark1");
    expect(pathToId("/sparkdash/energy")).toBe(ENERGY_ID);
    expect(pathToId("/sparkdash/")).toBe(OVERVIEW_ID);
    expect(pathToId("/sparkdash")).toBe(OVERVIEW_ID);
    expect(pathToId("/sparkdash/showcase/spark1")).toBeNull();
    expect(parsePath("/sparkdash/showcase/spark1")).toEqual({ mode: "showcase", showcaseSparkId: "spark1" });
  });

  it("round-trips every kind of page", () => {
    for (const id of [OVERVIEW_ID, ENERGY_ID, SHOWCASE_ID, "a b", benchId("tool-eval")]) expect(pathToId(idToPath(id))).toBe(id);
  });

  it("opens a deep link on its page", () => {
    window.history.replaceState(null, "", "/sparkdash/spark/spark2");
    expect(initialActiveId()).toBe("spark2");
    window.history.replaceState(null, "", "/");
  });

  it("opens the showcase in a new tab under the base", () => {
    const spark = { id: "spark1", llmPort: 8000, metrics: { llm: [{ available: true, modelId: "glm" }] } } as unknown as SparkSnapshot;
    expect(showcaseUrl(spark)).toBe("/sparkdash/showcase/spark1?port=8000&model=glm");
  });

  it("strips only the whole base segment", () => {
    expect(stripBase("/sparkdash/spark/x")).toBe("/spark/x");
    expect(stripBase("/sparkdashboard/x")).toBe("/sparkdashboard/x");
    expect(withBase("/")).toBe("/sparkdash/");
  });
});

describe("at the root, as in her own deployment", () => {
  it("is unchanged", () => {
    expect(idToPath("spark1")).toBe("/spark/spark1");
    expect(pathToId("/spark/spark1")).toBe("spark1");
    expect(stripBase("/spark/x")).toBe("/spark/x");
    expect(parsePath("/showcase/s1")).toEqual({ mode: "showcase", showcaseSparkId: "s1" });
  });
});
