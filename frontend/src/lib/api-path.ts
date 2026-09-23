const configuredBasePath = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

export const API_BASE_PATH = configuredBasePath.replace(/\/$/, "");

export function apiPath(path: string): string {
  return `${API_BASE_PATH}/${path.replace(/^\//, "")}`;
}
