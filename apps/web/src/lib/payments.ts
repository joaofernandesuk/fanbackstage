import { api } from "./api";

/** Complete the local development payment through the signed provider callback path. */
export async function completePaymentCheckout(paymentAttemptId: string): Promise<boolean> {
  await api(`/payments/development/${paymentAttemptId}/complete`, { method: "POST" });
  return true;
}
