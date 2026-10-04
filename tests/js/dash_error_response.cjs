'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const origin = 'http://127.0.0.1:8050';

function environment(response, error) {
  const calls = [];
  const pending = error ? Promise.reject(error) : Promise.resolve(response);
  const window = {
    location: {href: origin + '/maintenance', origin}, Request,
    fetch: function () {calls.push(Array.from(arguments)); return pending;}
  };
  const context = vm.createContext({window, URL});
  vm.runInContext(source, context);
  const wrapped = window.fetch;
  vm.runInContext(source, context);
  assert.equal(window.fetch, wrapped, 'reloading assets must not wrap twice');
  return {window, calls, pending};
}

async function repeatedText(input, init, status) {
  const response = new Response('{"error":"Login required"}', {
    status, headers: {'content-type': 'application/json', 'x-request-id': 'synthetic'}
  });
  const body = response.body;
  const headers = response.headers;
  const originalText = response.text;
  const env = environment(response);
  const actual = await env.window.fetch(input, init);
  assert.equal(actual, response);
  assert.equal(actual.status, status);
  assert.equal(actual.ok, false, 'authorization rejection must remain a rejection');
  assert.equal(actual.headers, headers);
  assert.equal(actual.body, body);
  assert.equal(actual.bodyUsed, false, 'the workaround must not consume eagerly');
  assert.notEqual(actual.text, originalText);
  const first = actual.text();
  const concurrent = actual.text();
  assert.equal(first, concurrent, 'concurrent text readers share one Promise');
  assert.equal(await first, '{"error":"Login required"}');
  assert.equal(actual.bodyUsed, true);
  assert.equal(actual.text(), first, 'the renderer can read again after await');
  assert.equal(await actual.text(), '{"error":"Login required"}');
  assert.equal(env.calls.length, 1, 'never retry a rejected operation');
  assert.equal(env.calls[0][0], input);
  assert.equal(env.calls[0][1], init);
}

async function unchanged(input, init, status) {
  const response = new Response('unchanged', {status});
  const originalText = response.text;
  const env = environment(response);
  const actual = await env.window.fetch(input, init);
  assert.equal(actual, response);
  assert.equal(actual.text, originalText);
  assert.equal(await actual.text(), 'unchanged');
  await assert.rejects(actual.text(), TypeError, 'native one-read behavior stays intact');
  assert.equal(env.calls.length, 1);
}

(async function () {
  // Native Response reproduces the renderer's consumed-body failure first.
  const native = new Response('denied', {status: 401});
  await native.text();
  await assert.rejects(native.text(), TypeError);
  await repeatedText('/_dash-update-component', {method: 'POST'}, 401);
  await repeatedText(new URL(origin + '/_dash-update-component?probe=1'), {method: 'post'}, 400);
  await repeatedText(new Request(origin + '/_dash-update-component', {method: 'POST'}), undefined, 401);
  await repeatedText(new Request(origin + '/_dash-update-component'), {method: 'POST'}, 400);
  await unchanged(new Request(origin + '/_dash-update-component', {method: 'POST'}), {method: 'GET'}, 401);
  for (const status of [200, 403, 404, 500]) {
    await unchanged('/_dash-update-component', {method: 'POST'}, status);
  }
  await unchanged('/_dash-update-component', undefined, 401);
  await unchanged('/_dash-update-component', {method: 'PUT'}, 400);
  await unchanged('/api/reports/export.csv', {method: 'POST'}, 401);
  await unchanged('/prefix/_dash-update-component', {method: 'POST'}, 401);
  await unchanged('/_dash-update-component-extra', {method: 'POST'}, 400);
  await unchanged('https://other.example.invalid/_dash-update-component', {method: 'POST'}, 401);
  await unchanged('http://127.0.0.1:8051/_dash-update-component', {method: 'POST'}, 400);

  const networkError = new TypeError('synthetic network rejection');
  const rejected = environment(null, networkError);
  await assert.rejects(rejected.window.fetch('/_dash-update-component', {method: 'POST'}),
    error => error === networkError);
  assert.equal(rejected.calls.length, 1);

  const streamError = new Error('synthetic response stream failure');
  const brokenBody = new ReadableStream({start(controller) {controller.error(streamError);}});
  const broken = new Response(brokenBody, {status: 401});
  const brokenEnv = environment(broken);
  const actual = await brokenEnv.window.fetch('/_dash-update-component', {method: 'POST'});
  const failedRead = actual.text();
  await assert.rejects(failedRead, error => error === streamError);
  assert.equal(actual.text(), failedRead);
  await assert.rejects(actual.text(), error => error === streamError);

  // Reading a different representation first must retain native failure behavior.
  const consumed = new Response('{"error":"Denied"}', {status: 401});
  const consumedEnv = environment(consumed);
  await consumedEnv.window.fetch('/_dash-update-component', {method: 'POST'});
  assert.deepEqual(await consumed.json(), {error: 'Denied'});
  await assert.rejects(consumed.text(), TypeError);
  console.log('dash error response contracts passed');
}()).catch(error => {console.error(error); process.exitCode = 1;});
