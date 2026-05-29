export class ApiError extends Error {
  status: number;
  code?: string;
  loginUrl?: string;

  constructor(message: string, status: number, code?: string, loginUrl?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.loginUrl = loginUrl;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  return requestJson<T>(path);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function apiDelete<T = unknown>(path: string): Promise<T> {
  return requestJson<T>(path, { method: 'DELETE' });
}

export async function apiPostForm<T>(path: string, body: FormData): Promise<T> {
  return requestJson<T>(path, {
    method: 'POST',
    body,
  });
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    ...init,
  });
  const payload = await parseJson(response);
  if (!response.ok) {
    const error = payload?.error;
    throw new ApiError(
      error?.message ?? `Request failed: ${response.status}`,
      response.status,
      error?.code,
      error?.loginUrl,
    );
  }
  return payload as T;
}

async function parseJson(response: Response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
