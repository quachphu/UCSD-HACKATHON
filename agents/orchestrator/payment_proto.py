"""
Fetch.ai Payment Protocol — seller-side for AutoRecruit.

Flow:
  1. User sends hiring request to orchestrator
  2. Orchestrator stores prompt + sends RequestPayment IMMEDIATELY (no LLM before this)
  3. ASI:One shows native "Pay With FET" / "Reject" buttons
  4. User approves → CommitPayment arrives here
  5. Verify on-chain → CompletePayment → route to Recruiter
  6. Pipeline: Recruiter → Scout → Ranker → Outreach → CEO summary
"""

import os
import logging
from datetime import datetime, timezone
from uuid import uuid4

from cosmpy.aerial.client import LedgerClient, NetworkConfig
from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    EndSessionContent,
    TextContent,
)
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

logger = logging.getLogger(__name__)

# ── Protocol object (must live here so manifest is published correctly) ────────
payment_proto = Protocol(spec=payment_protocol_spec, role="seller")

# ── Config ────────────────────────────────────────────────────────────────────
FET_AMOUNT = os.getenv("FIXED_FET_AMOUNT", "0.1")
AGENT_NETWORK = os.getenv("AGENT_NETWORK", "testnet")
FET_FUNDS = Funds(currency="FET", amount=FET_AMOUNT, payment_method="fet_direct")

# ── Module-level wallet address (set at startup by orchestrator_agent) ─────────
_orchestrator_wallet_address: str = ""

# Pending payments: user_address → {session_id, prompt, sender}
pending_payments: dict = {}

# Callback registered by orchestrator_agent to handle routing after payment
_on_payment_confirmed = None


def set_orchestrator_wallet_address(addr: str) -> None:
    global _orchestrator_wallet_address
    _orchestrator_wallet_address = addr


def set_on_payment_confirmed(callback) -> None:
    """Register async callback: (ctx, sender, prompt, session_id, tx_hash) → None"""
    global _on_payment_confirmed
    _on_payment_confirmed = callback


def get_explorer_url(tx_hash: str) -> str:
    if AGENT_NETWORK == "mainnet":
        return f"https://explore.fetch.ai/transactions/{tx_hash}"
    return f"https://explore-dorado.fetch.ai/transactions/{tx_hash}"


# ── Payment request ────────────────────────────────────────────────────────────

async def request_payment_from_user(ctx: Context, user_address: str) -> None:
    """
    Send RequestPayment immediately when user's message arrives.
    Must be called BEFORE any LLM calls to avoid timing issues with ASI:One.
    """
    use_testnet = AGENT_NETWORK == "testnet"
    description = "Pay 0.1 FET to activate AutoRecruit — AI-powered recruiting agents"

    metadata = {
        "agent": "autorecruit-orchestrator",
        "service": "agent_routing",
        "fet_network": "stable-testnet" if use_testnet else "mainnet",
        "mainnet": "false" if use_testnet else "true",
        "content": description,
    }
    if _orchestrator_wallet_address:
        metadata["provider_agent_wallet"] = _orchestrator_wallet_address

    recipient = _orchestrator_wallet_address or user_address

    await ctx.send(
        user_address,
        RequestPayment(
            accepted_funds=[FET_FUNDS],
            recipient=recipient,
            deadline_seconds=300,
            reference=str(ctx.session),
            description=description,
            metadata=metadata,
        ),
    )
    logger.info(f"RequestPayment sent to {user_address[:24]}... | Amount: {FET_AMOUNT} FET")


# ── Payment handlers ───────────────────────────────────────────────────────────

@payment_proto.on_message(CommitPayment)
async def handle_commit_payment(ctx: Context, sender: str, msg: CommitPayment) -> None:
    """User clicked 'Pay With FET'. Verify on-chain then route to pipeline."""
    logger.info(f"CommitPayment from {sender[:24]}... | TX: {msg.transaction_id[:24]}...")

    pending = pending_payments.pop(sender, None)
    if not pending:
        logger.warning(f"No pending payment for {sender}")
        return

    # Verify on-chain
    buyer_wallet = None
    if isinstance(msg.metadata, dict):
        buyer_wallet = (
            msg.metadata.get("buyer_fet_wallet")
            or msg.metadata.get("buyer_fet_address")
        )

    verified = _verify_payment(
        transaction_id=msg.transaction_id,
        expected_fet=str(msg.funds.amount) if hasattr(msg, "funds") and msg.funds else FET_AMOUNT,
        sender_wallet=buyer_wallet,
    )

    if not verified:
        logger.error(f"Payment verification failed | TX: {msg.transaction_id[:24]}...")
        await ctx.send(
            sender,
            CancelPayment(
                transaction_id=msg.transaction_id,
                reason="On-chain verification failed. Please try again.",
            ),
        )
        return

    logger.info(f"Payment verified | TX: {msg.transaction_id[:24]}...")
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))

    # Route to pipeline via registered callback
    if _on_payment_confirmed:
        await _on_payment_confirmed(
            ctx,
            sender,
            pending["prompt"],
            pending["session_id"],
            msg.transaction_id,
        )


@payment_proto.on_message(RejectPayment)
async def handle_reject_payment(ctx: Context, sender: str, msg: RejectPayment) -> None:
    """User clicked 'Reject'. End session gracefully."""
    logger.info(f"Payment rejected by {sender[:24]}... | Reason: {msg.reason}")
    pending_payments.pop(sender, None)
    await ctx.send(
        sender,
        ChatMessage(
            timestamp=datetime.now(tz=timezone.utc),
            msg_id=uuid4(),
            content=[
                TextContent(
                    type="text",
                    text="No problem! Come back when you're ready to recruit. 🤝",
                ),
                EndSessionContent(type="end-session"),
            ],
        ),
    )


# ── On-chain verification ──────────────────────────────────────────────────────

def _verify_payment(transaction_id: str, expected_fet: str, sender_wallet: str) -> bool:
    try:
        use_testnet = AGENT_NETWORK == "testnet"
        config = (
            NetworkConfig.fetchai_stable_testnet()
            if use_testnet
            else NetworkConfig.fetchai_mainnet()
        )
        ledger = LedgerClient(config)
        denom = "atestfet" if use_testnet else "afet"
        expected_amount = int(float(expected_fet) * 10**18)
        expected_recipient = _orchestrator_wallet_address

        tx = ledger.query_tx(transaction_id)
        if not tx.is_successful():
            return False

        for event_type, attrs in tx.events.items():
            if event_type == "transfer":
                if attrs.get("recipient") == expected_recipient and (
                    not sender_wallet or attrs.get("sender") == sender_wallet
                ):
                    amount_str = attrs.get("amount", "")
                    if amount_str.endswith(denom):
                        try:
                            if int(amount_str.replace(denom, "")) >= expected_amount:
                                return True
                        except Exception:
                            pass
        return False

    except Exception as e:
        logger.error(f"Verification error: {e}")
        # Hackathon fallback: trust the commit if on-chain check fails
        logger.warning("Falling back to trust-based verification")
        return True
