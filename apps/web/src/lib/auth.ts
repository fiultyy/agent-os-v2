/**
 * Frontend authentication utilities.
 *
 * Manages JWT access/refresh tokens in localStorage and provides a simple
 * reactive auth-state store for components to subscribe to.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// ── Storage keys ──────────────────────────────────────────────────────────────

const ACCESS_TOKEN_KEY = "agent_os_access_token";
const REFRESH_TOKEN_KEY = "agent_os_refresh_token";

// ── Token persistence ────────────────────────────────────────────────────────

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, access);
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh);
  notifyListeners(isAuthenticated());
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  notifyListeners(false);
}

// ── Auth state ────────────────────────────────────────────────────────────────

type AuthListener = (authenticated: boolean) => void;

const _listeners: Set<AuthListener> = new Set();

export function isAuthenticated(): boolean {
  return !!getAccessToken();
}

export function subscribe(listener: AuthListener): () => void {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

function notifyListeners(authenticated: boolean): void {
  _listeners.forEach((fn) => fn(authenticated));
}

// ── API calls ─────────────────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export async function login(apiKey: string): Promise<TokenResponse> {
  const res = await fetch(`${API_BASE}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Login failed (${res.status})`);
  }

  const data: TokenResponse = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return data;
}

export async function refresh(): Promise<TokenResponse | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!res.ok) {
    clearTokens();
    return null;
  }

  const data: TokenResponse = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return data;
}

export async function verify(token?: string): Promise<boolean> {
  const t = token ?? getAccessToken();
  if (!t) return false;

  try {
    const res = await fetch(`${API_BASE}/auth/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: t }),
    });
    const data = await res.json();
    return data.valid === true;
  } catch {
    return false;
  }
}
