import * as path from "node:path";
import Mocha from "mocha";

export function run(): Promise<void> {
  const mocha = new Mocha({ ui: "tdd", color: true, timeout: 60_000 });
  mocha.addFile(path.resolve(__dirname, "./smoke.test.js"));
  return new Promise((ok, fail) => mocha.run((failures) => (failures ? fail(new Error(`${failures} test(s) failed`)) : ok())));
}
