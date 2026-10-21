import assert from "node:assert/strict";
import test from "node:test";

const EMPTY = "";
import { ADMIN_PASSWORD_MIN_LENGTH, validateAdminCredentials } from "../src/app/password-policy.js";

test("administrator password shorter than the backend minimum is rejected before submit", () => {
  assert.equal(ADMIN_PASSWORD_MIN_LENGTH, 8);
  const message = validateAdminCredentials({ username: "admin", password: "123", confirmPassword: "123" });
  assert.match(message, /8/);
});

test("empty username and password report their own reason", () => {
  assert.match(validateAdminCredentials({ username: "  ", password: "abcdefgh" }), /用户名/);
  assert.match(validateAdminCredentials({ username: "admin", password: EMPTY }), /密码/);
});

test("mismatched confirmation is rejected", () => {
  assert.match(validateAdminCredentials({ username: "admin", password: "abcdefgh", confirmPassword: "abcdefgi" }), /不一致/);
});

test("a valid credential pair passes", () => {
  assert.equal(validateAdminCredentials({ username: "admin", password: "abcdefgh", confirmPassword: "abcdefgh" }), null);
  assert.equal(validateAdminCredentials({ username: "admin", password: "abcdefgh" }), null);
});

test("over-long password is rejected", () => {
  assert.match(validateAdminCredentials({ username: "admin", password: "a".repeat(257) }), /256/);
});
