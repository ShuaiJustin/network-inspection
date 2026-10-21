import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_THEME, STORAGE_KEY, applyTheme, nextTheme,
  normalizeTheme, persistTheme, readStoredTheme, resolveInitialTheme,
} from "../src/app/theme-preference.js";

const makeStorage = (initial = {}) => {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = v; },
  };
};

test("a saved preference always wins over the system setting", () => {
  assert.equal(resolveInitialTheme("light", true), "light");
  assert.equal(resolveInitialTheme("dark", false), "dark");
});

test("with no saved preference the system setting decides", () => {
  assert.equal(resolveInitialTheme(null, false), "light");
  assert.equal(resolveInitialTheme(null, true), "dark");
});

test("an unknown system answer falls back to the default theme", () => {
  assert.equal(resolveInitialTheme(null, undefined), DEFAULT_THEME);
  assert.equal(resolveInitialTheme("sepia", undefined), DEFAULT_THEME);
});

test("only known theme names are accepted", () => {
  assert.equal(normalizeTheme("light"), "light");
  assert.equal(normalizeTheme("dark"), "dark");
  assert.equal(normalizeTheme("solarized"), null);
  assert.equal(normalizeTheme(undefined), null);
});

test("toggling flips between the two themes", () => {
  assert.equal(nextTheme("dark"), "light");
  assert.equal(nextTheme("light"), "dark");
});

test("a stored preference round-trips", () => {
  const storage = makeStorage();
  assert.equal(persistTheme(storage, "light"), true);
  assert.equal(storage.data[STORAGE_KEY], "light");
  assert.equal(readStoredTheme(storage), "light");
});

test("a corrupted stored value is ignored rather than applied", () => {
  assert.equal(readStoredTheme(makeStorage({ [STORAGE_KEY]: "neon" })), null);
});

test("storage failures never break start-up", () => {
  const hostile = {
    getItem() { throw new Error("denied"); },
    setItem() { throw new Error("denied"); },
  };
  assert.equal(readStoredTheme(hostile), null);
  assert.equal(persistTheme(hostile, "dark"), false);
});

test("applying a theme drives the dark class on the root element", () => {
  const classes = new Set();
  const root = { classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) } };
  applyTheme(root, "dark");
  assert.ok(classes.has("dark"));
  applyTheme(root, "light");
  assert.ok(!classes.has("dark"));
});
