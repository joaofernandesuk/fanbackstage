const baseUrl = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";

export function apiMediaUrl(path: string): string {
  return `${baseUrl}/api/v1${path}`;
}

export type CurrentUser = {
  id: string;
  email: string;
  email_verified: boolean;
  adult_attested: boolean;
  country_code: string | null;
  roles: string[];
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly action?: string,
    readonly reason?: string,
    readonly retryable?: boolean,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { headers: requestHeaders, ...requestOptions } = options;
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    credentials: "include",
    ...requestOptions,
    headers: { "Content-Type": "application/json", ...requestHeaders },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new ApiError(
      typeof detail === "object" && detail !== null
        ? detail.message ?? "Unable to complete that request"
        : detail ?? "Unable to complete that request",
      response.status,
      typeof detail === "object" && detail !== null ? detail.code : undefined,
      typeof detail === "object" && detail !== null ? detail.action : undefined,
      typeof detail === "object" && detail !== null ? detail.reason : undefined,
      typeof detail === "object" && detail !== null ? detail.retryable : undefined,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
