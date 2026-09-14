import assert from 'node:assert/strict';
import test from 'node:test';

const SHA = '5dbda2127357b4be87821902d36e4ce9560f6876';
const url = `https://raw.githubusercontent.com/ORESoftware/ores-interfaces/${SHA}/contracts/ores-compose-machine/v1/authored.schema.json`;
const response = await fetch(url);
assert.equal(response.status, 200);
const defs = (await response.json()).$defs;
const codes = defs.MachineErrorResponse.properties.code.anyOf.map((x) => x.const);

test('activation failure has an explicit terminal job state and error code', () => {
  const states = defs.JobStatusResponse.properties.state.anyOf.map((x) => x.const);
  assert.ok(states.includes('failed'));
  assert.ok(codes.includes('activation_failed'));
});

test('stale switch workers have a dedicated fencing failure', () => {
  assert.ok(codes.includes('stale_generation'));
  assert.equal(defs.ActiveSystem.properties.generation.type, 'string');
  assert.equal(defs.ActiveSystem.properties.generation.pattern, '^[1-9][0-9]{0,19}$');
});

test('duplicate activation can converge on one job identity without inventing a second state machine', () => {
  assert.equal(defs.EnqueueResponse.properties.deduplicated.type, 'boolean');
  const states = defs.EnqueueResponse.properties.state.anyOf.map((x) => x.const);
  assert.deepEqual(states, ['queued','running']);
  assert.equal(defs.EnqueueResponse.properties.job_id.type, 'string');
});

test('readiness may remain false with no active system during recovery', () => {
  assert.equal(defs.ReadinessResponse.properties.ready.type, 'boolean');
  assert.equal(defs.ReadinessResponse.required.includes('active'), false);
  assert.equal(defs.ReadinessResponse.properties.active.$ref, '#/$defs/ActiveSystem');
});

test('ingress publication can fail closed independently of active-system identity', () => {
  assert.ok(codes.includes('ingress_unavailable'));
  assert.equal(defs.ActiveSystem.required.includes('ingress'), false);
  assert.equal(defs.ActiveSystem.properties.ingress.$ref, '#/$defs/MachineIngress');
});
