import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";

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

  it("accepts valid empty successful responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(api<void>("/messages/conversations/conversation-id/read", { method: "POST" })).resolves.toBeUndefined();
  });

  it("preserves a structured server error code for deterministic retry policy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "marketplace_payment_terminal",
        message: "Payment failed",
      },
    }), { status: 409 })));

    await expect(api("/marketplace/listings/item/checkout", { method: "POST" }))
      .rejects.toEqual(new ApiError("Payment failed", 409, "marketplace_payment_terminal"));
  });
});
