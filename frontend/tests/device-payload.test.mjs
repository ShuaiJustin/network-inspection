import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { buildDevicePayload } from "../src/app/device-payload.js";

test("clearing an optional text field sends null so the backend actually clears it", () => {
  const payload = buildDevicePayload(
    { name: "SW-01", group_name: "", note: "" },
    { isEditing: true },
  );
  // the backend uses exclude_unset=True: a dropped key keeps the old value
  assert.ok("group_name" in payload, "group_name must be sent");
  assert.equal(payload.group_name, null);
  assert.equal(payload.note, null);
});

test("blank secrets are dropped so the stored credential is kept", () => {
  const payload = buildDevicePayload(
    { ssh_password: "", enable_password: "", snmp_community: "", snmp_v3_auth_key: "" },
    { isEditing: true },
  );
  assert.equal("ssh_password" in payload, false);
  assert.equal("enable_password" in payload, false);
  assert.equal("snmp_community" in payload, false);
  assert.equal("snmp_v3_auth_key" in payload, false);
});

test("enable password is cleared only through the explicit null control", () => {
  const keep = buildDevicePayload({ enable_password: "" }, { isEditing: true });
  const clear = buildDevicePayload(
    { enable_password: "" },
    { isEditing: true, clearEnablePassword: true },
  );

  assert.equal("enable_password" in keep, false);
  assert.equal(clear.enable_password, null);
});

test("the SSH form exposes the optional Enable or Super password", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  assert.match(app, /name="enable_password"/);
  assert.match(app, /label="Enable \/ Super 密码"/);
  assert.match(app, /清除已保存的 Enable \/ Super 密码/);
  assert.match(app, /clearEnablePassword/);
});

test("on create, blank optional fields are omitted rather than sent as null", () => {
  const payload = buildDevicePayload(
    { ip: "10.0.0.1", group_name: "", note: "" },
    { isEditing: false },
  );
  assert.equal("group_name" in payload, false);
  assert.equal("note" in payload, false);
  assert.equal(payload.ip, "10.0.0.1");
});

test("non-empty values are always preserved", () => {
  const payload = buildDevicePayload(
    { name: "SW", group_name: "core", ssh_password: "secret" },
    { isEditing: true },
  );
  assert.equal(payload.name, "SW");
  assert.equal(payload.group_name, "core");
  assert.equal(payload.ssh_password, "secret");
});
