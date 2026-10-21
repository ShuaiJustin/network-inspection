import test from 'node:test';
import assert from 'node:assert/strict';
import { createUpdateController } from '../src/app/update-controller.js';

test('checks and downloads only once, tracks bytes, preserves download after install error', async () => {
  let finish;
  const calls = [];
  const controller = createUpdateController({
    invoke: async (command, args) => {
      calls.push(command);
      if (command === 'check_app_update') return { version: '2', currentVersion: '1', body: 'Changes' };
      if (command === 'download_app_update') {
        args.onEvent.onmessage({ event: 'Started', data: { contentLength: 10 } });
        args.onEvent.onmessage({ event: 'Progress', data: { chunkLength: 5 } });
        await new Promise(resolve => { finish = resolve; });
      }
      if (command === 'install_app_update') throw new Error('Tasks running');
    }, createChannel: () => ({}), getToken: () => 'session',
  });
  await controller.check();
  const downloading = controller.download();
  await controller.download();
  assert.equal(controller.getState().downloadedBytes, 5);
  finish(); await downloading;
  await controller.install();
  assert.equal(controller.getState().downloaded, true);
  assert.equal(controller.getState().status, 'ready');
  assert.match(controller.getState().error, /Tasks/);
  assert.equal(calls.filter(c => c === 'download_app_update').length, 1);
});

test('check and download errors can retry; checks cannot discard ready update', async () => {
  let fail = true;
  const controller = createUpdateController({ invoke: async command => {
    if (fail) throw new Error('offline');
    return command === 'check_app_update' ? { version: '2' } : undefined;
  }, createChannel: () => ({}), getToken: () => 'session' });
  await controller.check(); assert.equal(controller.getState().status, 'error');
  fail = false; await controller.check();
  fail = true; await controller.download(); assert.equal(controller.getState().status, 'error');
  fail = false; await controller.download(); await controller.check();
  assert.equal(controller.getState().status, 'ready');
});

test('browser never invokes IPC', async () => {
  const controller = createUpdateController({ invoke: null });
  await controller.check(); await controller.download(); await controller.install();
  assert.equal(controller.getState().status, 'unsupported');
});

test('a fresh session restores the verified download without downloading again', async () => {
  const calls = [];
  const controller = createUpdateController({
    invoke: async command => {
      calls.push(command);
      return { version: '2', downloaded: true };
    }, createChannel: () => ({}), getToken: () => 'new-session',
  });
  await controller.check();
  assert.equal(controller.getState().status, 'ready');
  assert.equal(controller.getState().downloaded, true);
  await controller.download();
  assert.deepEqual(calls, ['check_app_update']);
});

test('no available update reports current and never starts a download', async () => {
  const calls = [];
  const controller = createUpdateController({ invoke: async command => {
    calls.push(command);
    return null;
  }, createChannel: () => ({}), getToken: () => 'session' });
  await controller.check();
  assert.equal(controller.getState().status, 'current');
  assert.equal(controller.getState().update, null);
  await controller.download();
  assert.deepEqual(calls, ['check_app_update']);
});

test('concurrent checks share one pending operation', async () => {
  let finish;
  let calls = 0;
  const controller = createUpdateController({ invoke: async () => {
    calls += 1;
    return new Promise(resolve => { finish = resolve; });
  }, createChannel: () => ({}), getToken: () => 'session' });
  const checking = controller.check();
  assert.equal(controller.getState().status, 'checking');
  await controller.check();
  assert.equal(calls, 1);
  finish({ version: '2' });
  await checking;
  assert.equal(controller.getState().status, 'available');
});
