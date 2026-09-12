import { describe, expect, it } from "vitest";
import { decodeJwtPayload } from "../../src/auth/jwt";
const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
describe("decodeJwtPayload", () => {
  it("decodes the payload segment", () => {
    const tok = `${b64({ alg: "HS256" })}.${b64({ sub: "u1", email: "a@b", role: "operator", is_superadmin: false, type: "access", exp: 1 })}.sig`;
    expect(decodeJwtPayload(tok)).toMatchObject({ email: "a@b", role: "operator", is_superadmin: false });
  });
  it("returns undefined for garbage / API keys", () => {
    expect(decodeJwtPayload("tdt_abc")).toBeUndefined();
    expect(decodeJwtPayload("a.b")).toBeUndefined();
  });
});
