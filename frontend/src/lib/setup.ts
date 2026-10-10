import { apiPath } from "./api-path";
import { ApiError, getResponseErrorCode, readJsonPayload, unwrapSuccess } from "./api-errors";

export type SetupState = "NOT_READY" | "PENDING" | "COMPLETED";

export async function getSetupState(): Promise<SetupState> {
  let response: Response;
  try {
    response = await fetch(apiPath("/setup/status"), {
      cache: "no-store", credentials: "omit", headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR");
  }
  const payload = await readJsonPayload(response);
  if (!response.ok) {
    throw new ApiError(response.status, getResponseErrorCode(payload, response.status));
  }
  const data = unwrapSuccess<{ state: SetupState }>(payload, response.status);
  if (!["NOT_READY", "PENDING", "COMPLETED"].includes(data?.state)) {
    throw new ApiError(response.status, "INVALID_RESPONSE");
  }
  return data.state;
}

export async function submitSetup(values: {
  token: string;
  username: string;
  email: string;
  password: string;
}): Promise<void> {
  let response: Response;
  try {
    response = await fetch(apiPath("/setup/complete"), {
      method: "POST",
      credentials: "omit",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR");
  }
  const payload = await readJsonPayload(response);
  if (!response.ok) {
    throw new ApiError(response.status, getResponseErrorCode(payload, response.status));
  }
  unwrapSuccess(payload, response.status);
}
