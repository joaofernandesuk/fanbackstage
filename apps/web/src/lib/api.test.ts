import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("preserves JSON content type when a request adds an idempotency key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "subscription" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api("/subscriptions/creator/creator-id", {
      method: "POST",
      headers: { "Idempotency-Key": "subscription-command" },
      body: JSON.stringify({ duration: "month_1" }),
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/subscriptions/creator/creator-id",
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": "subscription-command",
        },
      }),
    );
  });
});
