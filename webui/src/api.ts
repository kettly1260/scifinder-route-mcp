import type { AdminState, JsonObject } from './types';

const SESSION_KEY = 'scifinderRouteAdminToken';
const LOCAL_KEY = 'scifinderRouteAdminTrustedToken';

export function getStoredToken(): string {
  return sessionStorage.getItem(SESSION_KEY) || localStorage.getItem(LOCAL_KEY) || '';
}

export function storeToken(token: string, trusted: boolean): void {
  sessionStorage.setItem(SESSION_KEY, token);
  if (trusted) {
    localStorage.setItem(LOCAL_KEY, token);
  } else {
    localStorage.removeItem(LOCAL_KEY);
  }
}

export function clearToken(): void {
  sessionStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(LOCAL_KEY);
}

export function hasTrustedToken(): boolean {
  return Boolean(localStorage.getItem(LOCAL_KEY));
}

async function parseResponse<T>(response: Response): Promise<T> {
  let data: JsonObject = {};
  try {
    data = (await response.json()) as JsonObject;
  } catch {
    data = { error: response.statusText };
  }
  if (!response.ok || data.error) {
    throw new Error(String(data.error || response.statusText));
  }
  return data as T;
}

export async function getJson<T>(url: string, token: string): Promise<T> {
  const response = await fetch(url, { headers: { 'X-Scifinder-Route-Token': token } });
  return parseResponse<T>(response);
}

export async function postJson<T>(url: string, token: string, payload: JsonObject = {}): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Scifinder-Route-Token': token },
    body: JSON.stringify(payload)
  });
  return parseResponse<T>(response);
}

export async function uploadFile(token: string, file: File): Promise<JsonObject> {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch('/api/upload', {
    method: 'POST',
    headers: { 'X-Scifinder-Route-Token': token },
    body: form
  });
  return parseResponse<JsonObject>(response);
}

export function loadState(token: string): Promise<AdminState> {
  return getJson<AdminState>('/api/state', token);
}

export async function getBlobUrl(url: string, token: string): Promise<string> {
  const response = await fetch(url, { headers: { 'X-Scifinder-Route-Token': token } });
  if (!response.ok) {
    throw new Error(`Failed to fetch image: ${response.statusText}`);
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}
