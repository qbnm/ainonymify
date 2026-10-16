import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate } from 'node:timers/promises';
import { initAccounts } from '../../frontend/js/accounts.js';

function setup(t, authenticate) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) {
      const listeners = new Map();
      elements.set(id, {
        value: '', textContent: '', hidden: false, disabled: false,
        append() {}, querySelectorAll: () => [],
        addEventListener: (name, fn) => listeners.set(name, fn),
        close: () => listeners.get('close')?.(),
      });
    }
    return elements.get(id);
  };
  const previousDocument = globalThis.document, previousLocation = globalThis.location;
  let reloads = 0;
  globalThis.document = {getElementById: element, querySelectorAll: () => []};
  globalThis.location = {reload: () => reloads++};
  t.after(() => {
    if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument;
    if (previousLocation === undefined) delete globalThis.location; else globalThis.location = previousLocation;
  });
  const ui = initAccounts({types: [], hasSession: () => false, api: (path, options) => {
    if (path === '/api/account') return Promise.resolve({json: async () => ({account: null})});
    return authenticate(path, options, element);
  }});
  return {element, ready: ui.ready, reloads: () => reloads};
}

for (const mode of ['login', 'register']) {
  for (const outcome of ['success', 'failure']) {
    test(`${mode}: clears password before request, and releases credentials after ${outcome}`, async t => {
      let resolve, reject, credentials;
      const request = new Promise((yes, no) => { resolve = yes; reject = no; });
      const ui = setup(t, (path, options, element) => {
        assert.equal(path, `/api/account/${mode}`);
        assert.equal(element('account-password').value, '');
        assert.equal(options.data.password, 'synthetic-test-password');
        credentials = options.data;
        return request;
      });
      await ui.ready;
      ui.element('account-username').value = 'synthetic-user';
      ui.element('account-password').value = 'synthetic-test-password';
      ui.element('account-auth-form').onsubmit({preventDefault() {}, submitter: {value: mode}});
      assert.equal(ui.element('account-password').value, '');
      if (outcome === 'success') resolve({}); else reject(new Error('Authentication failed'));
      await setImmediate();
      assert.equal(credentials.password, '');
      assert.equal(ui.element('account-password').value, '');
      assert.equal(ui.reloads(), outcome === 'success' ? 1 : 0);
      assert.equal(ui.element('account-feedback').textContent, outcome === 'success' ? '' : 'Authentication failed');
    });
  }
}

test('closing the dialog discards an unsubmitted password, including native close events', async t => {
  const ui = setup(t, () => { throw new Error('No request expected'); });
  await ui.ready;
  for (const close of [() => ui.element('account-close').onclick(), () => ui.element('continue-guest').onclick(), () => ui.element('account-dialog').close()]) {
    ui.element('account-password').value = 'unsubmitted-test-password';
    close();
    assert.equal(ui.element('account-password').value, '');
  }
});

test('synchronous request failure also releases the password', async t => {
  let credentials;
  const ui = setup(t, (_, options) => { credentials = options.data; throw new Error('Request failed'); });
  await ui.ready;
  ui.element('account-password').value = 'synthetic-test-password';
  ui.element('account-auth-form').onsubmit({preventDefault() {}});
  await setImmediate();
  assert.equal(ui.element('account-password').value, '');
  assert.equal(credentials.password, '');
  assert.equal(ui.element('account-feedback').textContent, 'Request failed');
});
