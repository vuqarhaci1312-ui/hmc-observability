import { describe, expect, it } from "vitest";
import { formatTime } from "../lib/time";

describe("time", () => {
  it("renders UTC timestamps in Asia/Baku", () => {
    expect(formatTime("2026-11-14T08:00:00Z")).toContain("12:00:00");
  });
});
