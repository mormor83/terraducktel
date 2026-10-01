import { describe, expect, it } from "vitest";
import { isPlainHttpToRemote, pickActive, readProfiles, uiUrlFor, warnOnPlainHttp } from "../../src/auth/profiles";

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

describe("readProfiles — map form (0.3.1 Settings-UI-native shape)", () => {
  it("reads name->url map entries and merges uiUrls / insecureList", () => {
    expect(readProfiles(
      { prod: "https://tdt.example.com///", staging: "https://staging.example.com" },
      { prod: "https://ui.example.com///" },
      ["staging"],
    )).toEqual([
      { name: "prod", url: "https://tdt.example.com", uiUrl: "https://ui.example.com", insecureTls: false },
      { name: "staging", url: "https://staging.example.com", uiUrl: undefined, insecureTls: true },
    ]);
  });

  it("drops map entries with a blank name or a non-http(s) url", () => {
    expect(readProfiles({ "": "https://a", b: "ftp://x", c: "not-a-url" })).toEqual([]);
  });

  it("sorts profiles by name", () => {
    expect(readProfiles({ zeta: "https://z", alpha: "https://a" }).map((p) => p.name)).toEqual(["alpha", "zeta"]);
  });

  it("still reads the legacy array form (no uiUrls/insecureList args)", () => {
    expect(readProfiles([{ name: "prod", url: "https://a" }])).toEqual([
      { name: "prod", url: "https://a", uiUrl: undefined, bu: undefined, insecureTls: false },
    ]);
  });

  it("when both a legacy array and map entries are present in the same raw value (VS Code's " +
     "cross-scope object merge of an un-migrated array with new map entries), the map wins for " +
     "a same-named profile and both are returned merged", () => {
    // Simulates what VS Code produces merging an old User-scope array with a new Workspace-scope
    // map: own properties "0" (the legacy row) and "prod" (the new map entry) on one object.
    const raw = { 0: { name: "prod", url: "https://legacy-prod" }, 1: { name: "legacy-only", url: "https://legacy-only" }, prod: "https://new-prod" };
    expect(readProfiles(raw)).toEqual([
      { name: "legacy-only", url: "https://legacy-only", uiUrl: undefined, bu: undefined, insecureTls: false },
      { name: "prod", url: "https://new-prod", uiUrl: undefined, insecureTls: false },
    ]);
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

describe("isPlainHttpToRemote", () => {
  it.each(["http://tdt.example.com", "http://10.0.0.5:8001/api", "http://192.168.1.2", "http://[2001:db8::1]:8001", "HTTP://tdt.example.com", "http://localhost.evil.com", "http://127.0.0.1.evil.com"])("flags %s", (u) => {
    expect(isPlainHttpToRemote(u)).toBe(true);
  });
  it.each(["https://tdt.example.com", "https://localhost", "http://localhost", "http://localhost:8001/api", "http://127.0.0.1:8001", "http://127.5.6.7", "http://[::1]:8001", "http://LOCALHOST:8001"])("does not flag %s", (u) => {
    expect(isPlainHttpToRemote(u)).toBe(false);
  });
  it("does not flag unparseable input (it is rejected elsewhere)", () => {
    expect(isPlainHttpToRemote("not a url")).toBe(false);
  });
});

describe("warnOnPlainHttp", () => {
  const remote = { name: "prod", url: "http://tdt.example.com" };
  const local = { name: "dev", url: "http://localhost:8001" };
  it("warns for plain http to a non-loopback host, naming the profile and host", () => {
    const shown: string[] = [];
    warnOnPlainHttp(remote, new Set(), (m) => shown.push(m));
    expect(shown).toHaveLength(1);
    expect(shown[0]).toContain("prod");
    expect(shown[0]).toContain("tdt.example.com");
  });
  it("stays silent for loopback http and for https", () => {
    const shown: string[] = [];
    warnOnPlainHttp(local, new Set(), (m) => shown.push(m));
    warnOnPlainHttp({ name: "x", url: "https://tdt.example.com" }, new Set(), (m) => shown.push(m));
    expect(shown).toEqual([]);
  });
  it("warns only once per profile+url (reload() runs on every settings change)", () => {
    const shown: string[] = [];
    const seen = new Set<string>();
    warnOnPlainHttp(remote, seen, (m) => shown.push(m));
    warnOnPlainHttp(remote, seen, (m) => shown.push(m));
    expect(shown).toHaveLength(1);
    warnOnPlainHttp({ ...remote, url: "http://other.example.com" }, seen, (m) => shown.push(m));
    expect(shown).toHaveLength(2);
  });
});
