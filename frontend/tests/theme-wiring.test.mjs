import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");

const tokenBlock = (head) => {
  const start = css.indexOf(head);
  return css.slice(start, css.indexOf("}", start));
};

test("index.css keeps the tailwind and theme layers wired in", () => {
  // shadcn components (Button/Input/Card/Checkbox) are styled entirely by tailwind
  // utilities and the theme token block. Dropping either import silently unstyles
  // every one of them while the app still builds and renders.
  assert.match(css, /@import\s+['"]\.\/tailwind\.css['"]/);
  assert.match(css, /@import\s+['"]\.\/theme\.css['"]/);
});

test("both themes define the same token set", () => {
  const names = (block) => new Set([...block.matchAll(/--c-([\w-]+):/g)].map((m) => m[1]));
  const light = names(tokenBlock(":root {"));
  const dark = names(tokenBlock(".dark {"));
  assert.ok(light.size > 20, "expected a full palette");
  assert.deepEqual([...light].sort(), [...dark].sort());
});

test("rules reference theme tokens instead of hardcoded colours", () => {
  // strip the two token blocks; nothing else may carry a raw colour, otherwise
  // that rule would look identical in both themes.
  const body = css
    .replace(/:root\s*\{[^}]*\}/, "")
    .replace(/\.dark\s*\{[^}]*\}/, "");
  assert.equal(/#[0-9a-fA-F]{3,8}\b/.test(body), false, "raw hex outside the token blocks");
  assert.equal(/rgba?\(/.test(body), false, "raw rgba outside the token blocks");
});

test("each theme declares its own colour-scheme", () => {
  assert.match(tokenBlock(":root {"), /color-scheme:\s*light/);
  assert.match(tokenBlock(".dark {"), /color-scheme:\s*dark/);
});
