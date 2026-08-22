"""Protect immutable referral allocation snapshots."""

from alembic import op

revision = "20260822_0022"
down_revision = "20260822_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_referral_allocation_within_platform_fee",
        "referral_commission_allocations",
        "amount_minor <= platform_fee_minor",
    )
    op.create_check_constraint(
        "ck_referral_allocation_beneficiary",
        "referral_commission_allocations",
        "(beneficiary_actor_type = 'creator' AND beneficiary_creator_id IS NOT NULL "
        "AND beneficiary_user_id IS NULL AND beneficiary_affiliate_partner_id IS NULL) OR "
        "(beneficiary_actor_type = 'user' AND beneficiary_user_id IS NOT NULL "
        "AND beneficiary_creator_id IS NULL AND beneficiary_affiliate_partner_id IS NULL) OR "
        "(beneficiary_actor_type = 'affiliate_partner' "
        "AND beneficiary_affiliate_partner_id IS NOT NULL AND beneficiary_creator_id IS NULL "
        "AND beneficiary_user_id IS NULL)",
    )
    op.execute(
        """
        CREATE FUNCTION prevent_referral_allocation_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Referral allocation history is immutable';
          END IF;
          IF NEW.source_ledger_transaction_id IS DISTINCT FROM OLD.source_ledger_transaction_id
             OR NEW.signup_attribution_id IS DISTINCT FROM OLD.signup_attribution_id
             OR NEW.policy_id IS DISTINCT FROM OLD.policy_id
             OR NEW.beneficiary_actor_type IS DISTINCT FROM OLD.beneficiary_actor_type
             OR NEW.beneficiary_user_id IS DISTINCT FROM OLD.beneficiary_user_id
             OR NEW.beneficiary_creator_id IS DISTINCT FROM OLD.beneficiary_creator_id
             OR NEW.beneficiary_affiliate_partner_id IS DISTINCT FROM OLD.beneficiary_affiliate_partner_id
             OR NEW.revenue_type IS DISTINCT FROM OLD.revenue_type
             OR NEW.currency IS DISTINCT FROM OLD.currency
             OR NEW.platform_fee_minor IS DISTINCT FROM OLD.platform_fee_minor
             OR NEW.amount_minor IS DISTINCT FROM OLD.amount_minor
             OR NEW.policy_snapshot IS DISTINCT FROM OLD.policy_snapshot
             OR NEW.allocated_at IS DISTINCT FROM OLD.allocated_at THEN
            RAISE EXCEPTION 'Referral allocation history is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER referral_commission_allocations_snapshot_immutable "
        "BEFORE UPDATE OR DELETE ON referral_commission_allocations "
        "FOR EACH ROW EXECUTE FUNCTION prevent_referral_allocation_snapshot_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS prevent_referral_allocation_snapshot_mutation() CASCADE")
    op.drop_constraint(
        "ck_referral_allocation_beneficiary", "referral_commission_allocations", type_="check"
    )
    op.drop_constraint(
        "ck_referral_allocation_within_platform_fee",
        "referral_commission_allocations",
        type_="check",
    )
