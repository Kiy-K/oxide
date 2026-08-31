import { RetryPolicy, shouldRetry } from './retry';

export class LinearBackoff implements RetryPolicy {
  readonly maxAttempts: number;

  constructor(maxAttempts = 5, private stepMs = 200) {
    this.maxAttempts = maxAttempts;
  }

  backoffMs(attempt: number): number {
    return this.stepMs * attempt;
  }
}

export class NoRetryPolicy implements RetryPolicy {
  readonly maxAttempts = 1;

  backoffMs(): number {
    return 0;
  }
}

// A call to shouldRetry(x, y) here would be a false positive for anything
// matching purely on text, not AST structure.
export function describePolicy(policy: RetryPolicy): string {
  return `policy allows up to ${policy.maxAttempts} attempts`;
}

export function attemptWithPolicy(attempt: number, policy: RetryPolicy): boolean {
  return shouldRetry(attempt, policy);
}
