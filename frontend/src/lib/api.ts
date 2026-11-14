import { useUi } from "../stores/ui";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const token = useUi.getState().token;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers, credentials: "include" });
  if (response.status === 401 && !path.includes("/auth/login") && !path.includes("/auth/refresh")) {
    const refreshed = await fetch("/api/v1/auth/refresh", { method: "POST", credentials: "include" });
    if (refreshed.ok) {
      const payload = await refreshed.json();
      useUi.getState().setToken(payload.access_token);
      headers.set("Authorization", `Bearer ${payload.access_token}`);
      const retry = await fetch(path, { ...init, headers, credentials: "include" });
      if (!retry.ok) throw new ApiError(retry.status, await retry.text());
      return retry.json();
    }
    useUi.getState().setToken(null);
    useUi.getState().setUser(null);
  }
  if (!response.ok) throw new ApiError(response.status, await response.text());
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function queryString(params: Record<string, string | number | undefined | null>) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}
