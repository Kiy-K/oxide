export interface RetryPolicy {
  maxAttempts: number;
  backoffMs(attempt: number): number;
}

/** Should the failed request be attempted again? */
export function shouldRetry(attempt: number, policy: RetryPolicy): boolean {
  return attempt < policy.maxAttempts;
}

export class ExponentialBackoff implements RetryPolicy {
  readonly maxAttempts: number;

  constructor(maxAttempts = 3, private baseMs = 100) {
    this.maxAttempts = maxAttempts;
  }

  /** Delay grows exponentially with the attempt count. */
  backoffMs(attempt: number): number {
    return this.baseMs * 2 ** attempt;
  }
}

export const defaultRetryPolicy: RetryPolicy = new ExponentialBackoff(3, 150);
