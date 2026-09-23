import { describe, expect, it } from "vitest";

import { ageFrom, dueDateFrom, humanize, initials } from "./format";

describe("format helpers", () => {
  it("computes age in whole years, respecting birthdays", () => {
    const today = new Date("2026-09-24T12:00:00");
    expect(ageFrom("1980-09-24", today)).toBe(46);
    expect(ageFrom("1980-09-25", today)).toBe(45);
    expect(ageFrom(null, today)).toBeNull();
  });

  it("humanizes enum values for labels only", () => {
    expect(humanize("pending_confirmation")).toBe("Pending confirmation");
    expect(humanize(null)).toBe("—");
  });

  it("builds initials and handles missing names", () => {
    expect(initials("Placeholder Person")).toBe("PP");
    expect(initials(null)).toBe("?");
  });

  it("computes follow-up due dates", () => {
    const from = new Date("2026-01-31T10:00:00");
    expect(dueDateFrom(2, "weeks", from)).toBe("2026-02-14");
    expect(dueDateFrom(10, "days", from)).toBe("2026-02-10");
  });
});
