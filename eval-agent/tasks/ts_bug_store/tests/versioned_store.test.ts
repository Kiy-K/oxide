import { test } from 'node:test';
import assert from 'node:assert';
import { VersionedStore } from '../src/versioned_store.ts';
import { AuditLog } from '../src/audit.ts';

test('set increments version per key', () => {
  const s = new VersionedStore<number>();
  assert.equal(s.set('k', 1), 1);
  assert.equal(s.set('k', 2), 2);
  assert.equal(s.versionOf('k'), 2);
});

test('independent keys start at initial version', () => {
  const s = new VersionedStore<number>(10);
  assert.equal(s.set('a', 5), 10);
  assert.equal(s.set('b', 6), 10);
});

test('audit log versions each append', () => {
  const log = new AuditLog();
  const v1 = log.record('e1', 'first');
  const v2 = log.record('e1', 'second');
  assert.notEqual(v1, v2);
});
