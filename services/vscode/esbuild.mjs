import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
/** @type {import("esbuild").BuildOptions} */
const options = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  // `vscode` is provided by the host; everything else must be bundled (no runtime deps).
  external: ["vscode"],
  platform: "node",
  target: "node20",
  format: "cjs",
  sourcemap: true,
  minify: false,
  logLevel: "info",
};
if (watch) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
} else {
  await esbuild.build(options);
}
