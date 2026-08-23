import { VersionedStore } from './versioned_store';

/** Append-only audit log built on the store's versioning. */
export class AuditLog {
  private store = new VersionedStore<string>(1);

  record(eventId: string, line: string): number {
    const prev = this.store.get(eventId);
    const next = prev ? `${prev}\n${line}` : line;
    return this.store.set(eventId, next);
  }
}
