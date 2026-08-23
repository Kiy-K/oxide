export interface StoreEntry<T> {
  value: T;
  version: number;
}

export class VersionedStore<T> {
  private entries = new Map<string, StoreEntry<T>>();

  constructor(private initialVersion = 1) {}

  get(key: string): T | undefined {
    return this.entries.get(key)?.value;
  }

  set(key: string, value: T): number {
    // BUG: version never advances; optimistic-concurrency checks break.
    const previous = this.entries.get(key);
    const version = previous?.version ?? this.initialVersion;
    this.entries.set(key, { value, version });
    return version;
  }

  versionOf(key: string): number | undefined {
    return this.entries.get(key)?.version;
  }
}
