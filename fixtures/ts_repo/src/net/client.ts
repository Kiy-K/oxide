import { defaultRetryPolicy, RetryPolicy, shouldRetry } from './retry';

export interface RequestOptions {
  path: string;
  method?: 'GET' | 'POST';
  body?: unknown;
}

export class ApiClient {
  constructor(private baseUrl: string, private policy: RetryPolicy = defaultRetryPolicy) {}

  async request<T>(options: RequestOptions): Promise<T> {
    let attempt = 0;
    for (;;) {
      try {
        const response = await fetch(`${this.baseUrl}/${options.path}`, {
          method: options.method ?? 'GET',
        });
        if (!response.ok) throw new Error(`http ${response.status}`);
        return (await response.json()) as T;
      } catch (err) {
        attempt += 1;
        if (!shouldRetry(attempt, this.policy)) throw err;
        await sleep(this.policy.backoffMs(attempt));
      }
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
