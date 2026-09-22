import test from 'node:test';
import assert from 'node:assert/strict';
import {label, statusTone, workActions} from '../src/model.js';
import {createApi} from '../src/api.js';

test('presentation helpers expose ISRP states cleanly', () => {
  assert.equal(label('READY_TO_CLOSE'), 'Ready To Close');
  assert.equal(statusTone('IN_REVIEW'), 'accent');
  assert.deepEqual(workActions({state: 'READY'}), ['start']);
  assert.deepEqual(workActions({state: 'IN_PROGRESS'}), ['complete']);
});

test('API client sends assessment under the selected request', async () => {
  let called;
  const client = createApi(async (path, options) => {
    called = {path, options};
    return {ok: true, json: async () => ({id: 9})};
  });
  const original = globalThis.crypto;
  Object.defineProperty(globalThis, 'crypto', {
    value: {randomUUID: () => 'command-1'}, configurable: true,
  });
  try {
    await client.createAssessment({id: 7, revision: 3}, {
      title: 'Application assessment', assessment_type: 'APPLICATION',
    });
  } finally {
    Object.defineProperty(globalThis, 'crypto', {value: original, configurable: true});
  }
  assert.equal(called.path, '/api/requests/7/assessments');
  assert.deepEqual(JSON.parse(called.options.body), {
    command_id: 'command-1', expected_revision: 3,
    title: 'Application assessment', assessment_type: 'APPLICATION',
  });
});
