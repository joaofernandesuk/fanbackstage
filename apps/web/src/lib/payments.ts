import { api } from "./api";

type Checkout = {
  action: "development_complete" | "staging_sandbox_checkout";
  status: string;
};

const wait = (milliseconds: number) => new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

/**
 * Browser-side checkout dispatch only. Staging completion queues a fictitious
 * processor callback; it never settles the attempt from the browser.
 */
export async function completePaymentCheckout(paymentAttemptId: string): Promise<boolean> {
  const checkout = await api<Checkout>(`/payments/${paymentAttemptId}/checkout`);
  if (checkout.action === "development_complete") {
    await api(`/payments/development/${paymentAttemptId}/complete`, { method: "POST" });
    return true;
  }
  if (checkout.action !== "staging_sandbox_checkout") {
    return false;
  }
  await api(`/payments/staging-sandbox/${paymentAttemptId}/checkout`, {
    method: "POST",
    body: JSON.stringify({ outcome: "SUCCESS" }),
  });
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await wait(250);
    const current = await api<Checkout>(`/payments/${paymentAttemptId}/checkout`);
    if (current.status !== "pending") return true;
  }
  throw new Error("The sandbox payment callback did not complete in time.");
}
