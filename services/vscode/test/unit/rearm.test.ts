import { describe, expect, it } from "vitest";
import { createRearm } from "../../src/notifications/rearm";

describe("createRearm", () => {
  it("primes once per key and starts", async () => {
    const calls: string[] = [];
    let key: string | undefined = "local:default";
    const rearm = createRearm({
      key: () => key,
      prime: async () => { calls.push("prime"); },
      start: () => calls.push("start"),
      stop: () => calls.push("stop"),
    });
    await rearm();
    // stop() precedes prime(): the previous key's timer must not poll during the prime.
    expect(calls).toEqual(["stop", "prime", "start"]);
    calls.length = 0;
    await rearm();                             // same key: no re-prime, still (re)starts
    expect(calls).toEqual(["start"]);
  });

  it("stops and forgets the primed key when signed out", async () => {
    const calls: string[] = [];
    let key: string | undefined = "local:default";
    const rearm = createRearm({ key: () => key, prime: async () => { calls.push("prime"); }, start: () => calls.push("start"), stop: () => calls.push("stop") });
    await rearm();
    calls.length = 0;
    key = undefined;
    await rearm();
    expect(calls).toEqual(["stop"]);
    calls.length = 0;
    key = "local:default";                     // signing back in re-primes (primedFor was cleared)
    await rearm();
    expect(calls).toEqual(["stop", "prime", "start"]);
  });

  it("does not start() when a sign-out lands while prime() is still in flight", async () => {
    const calls: string[] = [];
    let key: string | undefined = "local:default";
    let releasePrime!: () => void;
    const gate = new Promise<void>((r) => { releasePrime = r; });
    const rearm = createRearm({
      key: () => key,
      prime: async () => { calls.push("prime"); await gate; },
      start: () => calls.push("start"),
      stop: () => calls.push("stop"),
    });
    const p1 = rearm();                        // seq=1: enters prime(), then hangs on the gate
    await new Promise((r) => setTimeout(r, 0)); // let it reach `await d.prime()`
    key = undefined;                           // sign-out races the still-in-flight prime
    releasePrime();
    await p1;
    expect(calls).toEqual(["stop", "prime"]);  // must NOT have start()ed with a signed-out session
  });

  it("does not start() when a later rearm (profile/BU switch) supersedes an in-flight one", async () => {
    const calls: string[] = [];
    let key: string | undefined = "a:default";
    let releaseFirstPrime!: () => void;
    const gate = new Promise<void>((r) => { releaseFirstPrime = r; });
    let primeCall = 0;
    const rearm = createRearm({
      key: () => key,
      prime: async () => { primeCall++; calls.push(`prime:${key}`); if (primeCall === 1) await gate; },
      start: () => calls.push(`start:${key}`),
      stop: () => calls.push("stop"),
    });
    const p1 = rearm();                        // seq=1: primes for "a:default", hangs
    await new Promise((r) => setTimeout(r, 0));
    key = "b:default";                         // profile switch before p1's prime() resolves
    const p2 = rearm();                        // seq=2: primes for "b:default" (different key), runs to completion
    await p2;
    expect(calls).toEqual(["stop", "prime:a:default", "stop", "prime:b:default", "start:b:default"]);
    releaseFirstPrime();
    await p1;                                  // p1 finally resolves, but must not start() again — it was superseded
    expect(calls).toEqual(["stop", "prime:a:default", "stop", "prime:b:default", "start:b:default"]);
  });
});
