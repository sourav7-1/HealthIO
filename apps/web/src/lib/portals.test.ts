import { describe, expect, it } from "vitest";

import { portals } from "./portals";

describe("portals", () => {
  it("has one entry per role with a unique route", () => {
    expect(portals.map((p) => p.href).sort()).toEqual(["/admin", "/caregiver", "/doctor", "/patient"]);
  });
});
