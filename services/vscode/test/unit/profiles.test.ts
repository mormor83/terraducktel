import { describe, expect, it } from "vitest";
import { pickActive, readProfiles, uiUrlFor } from "../../src/auth/profiles";

describe("readProfiles", () => {
  it("returns [] for non-array input", () => {
    expect(readProfiles(undefined)).toEqual([]);
    expect(readProfiles(null)).toEqual([]);
    expect(readProfiles({})).toEqual([]);
    expect(readProfiles("nope")).toEqual([]);
  });

  it("drops non-object entries", () => {
    expect(readProfiles([null, undefined, 42, "x", true, { name: "prod", url: "https://a" }])).toEqual([
      { name: "prod", url: "https://a", uiUrl: undefined, bu: undefined, insecureTls: false },
    ]);
  });

  it("drops entries missing a name or with a blank name", () => {
    expect(readProfiles([{ url: "https://a" }, { name: "", url: "https://a" }, { name: "   ", url: "https://a" }])).toEqual([]);
  });

  it("drops entries missing a url or with a non-http(s) url", () => {
    expect(readProfiles([
      { name: "a" },
      { name: "b", url: "ftp://x" },
      { name: "c", url: "not-a-url" },
      { name: "d", url: 42 },
    ])).toEqual([]);
  });

  it("trims the name", () => {
    expect(readProfiles([{ name: "  prod  ", url: "https://a" }])).toEqual([
      { name: "prod", url: "https://a", uiUrl: undefined, bu: undefined, insecureTls: false },
    ]);
  });

  it("strips trailing slashes from url and uiUrl", () => {
    expect(readProfiles([{ name: "prod", url: "https://a///", uiUrl: "https://ui///" }])).toEqual([
      { name: "prod", url: "https://a", uiUrl: "https://ui", bu: undefined, insecureTls: false },
    ]);
  });

  it("carries bu through when a non-empty string", () => {
    expect(readProfiles([{ name: "prod", url: "https://a", bu: "team-a" }])[0].bu).toBe("team-a");
    expect(readProfiles([{ name: "prod", url: "https://a", bu: "" }])[0].bu).toBeUndefined();
    expect(readProfiles([{ name: "prod", url: "https://a", bu: 42 }])[0].bu).toBeUndefined();
  });

  it("insecureTls is true only when strictly === true", () => {
    expect(readProfiles([{ name: "a", url: "https://a", insecureTls: true }])[0].insecureTls).toBe(true);
    expect(readProfiles([{ name: "a", url: "https://a", insecureTls: "true" }])[0].insecureTls).toBe(false);
    expect(readProfiles([{ name: "a", url: "https://a", insecureTls: 1 }])[0].insecureTls).toBe(false);
    expect(readProfiles([{ name: "a", url: "https://a" }])[0].insecureTls).toBe(false);
  });
});

describe("uiUrlFor", () => {
  it("uses uiUrl when present", () => {
    expect(uiUrlFor({ name: "p", url: "https://api.example.com/api", uiUrl: "https://ui.example.com" })).toBe("https://ui.example.com");
  });
  it("strips a trailing /api from url when uiUrl is absent", () => {
    expect(uiUrlFor({ name: "p", url: "https://example.com/api" })).toBe("https://example.com");
  });
  it("returns the url unchanged when it has no trailing /api", () => {
    expect(uiUrlFor({ name: "p", url: "https://example.com" })).toBe("https://example.com");
  });
});

describe("pickActive", () => {
  const profiles = [
    { name: "prod", url: "https://a" },
    { name: "staging", url: "https://b" },
  ];
  it("picks the profile matching activeName", () => {
    expect(pickActive(profiles, "staging")).toEqual(profiles[1]);
  });
  it("falls back to the first profile when activeName doesn't match", () => {
    expect(pickActive(profiles, "nope")).toEqual(profiles[0]);
    expect(pickActive(profiles, undefined)).toEqual(profiles[0]);
  });
  it("returns undefined when there are no profiles", () => {
    expect(pickActive([], "prod")).toBeUndefined();
  });
});
