from uuid import UUID

from pydantic import BaseModel, Field


class PurchaseResponse(BaseModel):
    id: UUID
    content_id: UUID
    status: str
    gross_amount_minor: int
    platform_fee_minor: int
    creator_amount_minor: int
    currency: str
    payment_attempt_id: UUID


class DevelopmentPaymentCompletionResponse(BaseModel):
    """Stable completion response for non-PPV uses of the shared payment path."""

    id: UUID
    status: str
    payment_attempt_id: UUID


class PaymentCheckoutResponse(BaseModel):
    payment_attempt_id: UUID
    provider: str
    provider_reference: str
    action: str
    status: str


class StagingPaymentCheckoutInput(BaseModel):
    outcome: str = Field(pattern="^(SUCCESS|DECLINE|DELAYED_SUCCESS|REFUND|DISPUTE|CHARGEBACK)$")


class PaymentOperationResponse(BaseModel):
    id: UUID
    status: str
    payment_attempt_id: UUID


class RefundRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class FinanceRefundOperationInput(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
    confirmed: bool


class FinanceReconciliationInput(BaseModel):
    confirmed: bool
    limit: int = Field(default=25, ge=1, le=100)


class CommissionUpdate(BaseModel):
    basis_points: int = Field(ge=0, le=10_000)


class CreatorEarningsResponse(BaseModel):
    pending_amount_minor: int
    available_amount_minor: int
    currency: str
    ppv_gross_amount_minor: int = 0
    platform_fee_amount_minor: int = 0
    creator_net_amount_minor: int = 0
    marketplace_net_amount_minor: int = 0


class PurchaseHistoryResponse(BaseModel):
    id: UUID
    content_id: UUID
    content_title: str
    creator_username: str
    gross_amount_minor: int
    currency: str
    status: str
