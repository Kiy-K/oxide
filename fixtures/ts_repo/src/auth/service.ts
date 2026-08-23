import { ApiClient } from '../net/client';

export interface Session {
  sessionId: string;
  token: string;
}

/** Decode the payload of a JWT without verifying its signature. */
export function decodeJwt(token: string): Record<string, unknown> {
  const [, payload] = token.split('.');
  const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
  return JSON.parse(json);
}

export class AuthService {
  constructor(private client: ApiClient) {}

  async login(username: string, password: string): Promise<Session> {
    const session = await this.client.request<Session>({
      path: 'auth/login',
      method: 'POST',
      body: { username, password },
    });
    return session;
  }

  /** Exchange a stale token for a fresh one once it expires. */
  async refreshToken(sessionId: string): Promise<Session> {
    return this.client.request<Session>({ path: `auth/${sessionId}/refresh` });
  }
}
