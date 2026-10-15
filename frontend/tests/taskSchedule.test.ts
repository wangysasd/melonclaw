import { expect, it } from "vitest";
import { localToUtc, scheduleLabel, zonedLocal } from "../src/components/tasks/schedule";

it("always interprets local input in Beijing time", () => {
  expect(localToUtc("2026-10-14T09:00")).toBe("2026-10-14T01:00:00.000Z");
  expect(localToUtc("2026-10-14T00:00")).toBe("2026-10-13T16:00:00.000Z");
  expect(zonedLocal("2026-10-14T01:00:00Z")).toBe("2026-10-14T09:00");
});

it("rejects incomplete or invalid dates", () => {
  for (const value of ["", "2026-10-14", "2026-02-30T09:00", "2026-10-14T25:00"]) {
    expect(() => localToUtc(value)).toThrow();
  }
});

it("labels all four schedule kinds without timezone configuration", () => {
  expect(scheduleLabel({ kind: "manual" })).toBe("手动运行");
  expect(scheduleLabel({ kind: "daily", local_time: "09:00" })).toBe("每天 09:00");
  expect(scheduleLabel({ kind: "weekly", weekday: 6, local_time: "09:00" })).toBe("每周日 09:00");
  expect(scheduleLabel({ kind: "once", run_at: "2026-10-14T01:00:00Z" })).toBe("一次 · 2026-10-14 09:00");
});
