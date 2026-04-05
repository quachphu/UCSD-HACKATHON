import os
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4
from typing import Dict, Any

from dotenv import load_dotenv
load_dotenv()

from uagents import Agent, Context, Protocol
from uagents.setup import fund_agent_if_low
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
    chat_protocol_spec,
)
from openai import OpenAI

from agents.orchestrator.payment_proto import (
    payment_proto,
    pending_payments,
    request_payment_from_user,
    set_orchestrator_wallet_address,
    set_on_payment_confirmed,
    get_explorer_url,
)
from agents.models.shared_models import (
    RecruitmentRequest,
    OutreachSummary,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Specialist agent addresses ────────────────────────────────────────────────
RECRUITER_ADDRESS = os.getenv("RECRUITER_ADDRESS", "")
SCOUT_ADDRESS     = os.getenv("SCOUT_ADDRESS", "")
RANKER_ADDRESS    = os.getenv("RANKER_ADDRESS", "")
OUTREACH_ADDRESS  = os.getenv("OUTREACH_ADDRESS", "")

# ── ASI:One client ────────────────────────────────────────────────────────────
asi_client = OpenAI(
    base_url="https://api.asi1.ai/v1",
    api_key=os.getenv("ASI1_API_KEY"),
)

# ── Agent setup ───────────────────────────────────────────────────────────────
agent = Agent(
    name="autorecruit-orchestrator",
    seed=os.getenv("ORCHESTRATOR_SEED", "autorecruit-orchestrator-diamond-2026-phu"),
    port=8001,
    mailbox=True,
    publish_agent_details=True,
    network="testnet",
)

# Wallet address stored at module level so handlers can access it
_wallet_address: str = str(agent.wallet.address())

# Session storage: session_id → {sender, tx_hash, prompt}
sessions: Dict[str, Any] = {}


def classify_recruitment_request(prompt: str) -> dict:
    """Extract structured hiring params from CEO's NL message via ASI:One."""
    try:
        response = asi_client.chat.completions.create(
            model="asi1-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Extract hiring parameters from this CEO message: '{prompt}'\n"
                    "Return ONLY valid JSON, no markdown, no explanation:\n"
                    '{"role": "job title", "count": number, '
                    '"location": "city", "urgency": "timeline"}\n'
                    "Defaults if not specified: count=1, location='Los Angeles', "
                    "urgency='as soon as possible'"
                )
            }],
            max_tokens=200,
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {
            "role": "AI Engineer",
            "count": 1,
            "location": "Los Angeles",
            "urgency": "as soon as possible",
        }


async def on_payment_confirmed(
    ctx: Context,
    sender: str,
    prompt: str,
    session_id: str,
    tx_hash: str,
) -> None:
    """Called by payment_proto after payment is verified. Routes to Recruiter."""
    sessions[session_id] = {
        "sender": sender,
        "tx_hash": tx_hash,
        "prompt": prompt,
    }

    params = classify_recruitment_request(prompt)
    logger.info(f"Classified: {params}")

    await ctx.send(
        RECRUITER_ADDRESS,
        RecruitmentRequest(
            role=params["role"],
            count=int(params.get("count", 1)),
            location=params.get("location", "Los Angeles"),
            urgency=params.get("urgency", "as soon as possible"),
            session_id=session_id,
        ),
    )
    logger.info(f"RecruitmentRequest sent to Recruiter for session {session_id}")


# Register callback into payment_proto so it can route after payment
set_on_payment_confirmed(on_payment_confirmed)


# ── Chat Protocol (ASI:One entry point) ───────────────────────────────────────
chat_proto = Protocol(spec=chat_protocol_spec)


@chat_proto.on_message(ChatMessage)
async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
    """
    Receives CEO message from ASI:One.
    IMMEDIATELY sends RequestPayment — no LLM calls before this.
    """
    user_text = ""
    for item in msg.content:
        if hasattr(item, "text") and item.text:
            user_text = item.text.strip()
            break

    if not user_text:
        return

    session_id = str(msg.msg_id)

    # 1. Acknowledge immediately
    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc),
            acknowledged_msg_id=msg.msg_id,
        ),
    )

    # 2. Store pending payment — no LLM calls yet
    pending_payments[sender] = {
        "session_id": session_id,
        "prompt": user_text,
        "sender": sender,
    }

    # 3. Send RequestPayment IMMEDIATELY
    await request_payment_from_user(ctx, sender)

    logger.info(f"Payment requested from {sender} for session {session_id}")


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
    pass


# ── OutreachSummary handler (end of pipeline) ─────────────────────────────────
@agent.on_message(OutreachSummary)
async def handle_outreach_summary(ctx: Context, sender: str, msg: OutreachSummary) -> None:
    """Pipeline complete — send final report to CEO on ASI:One."""
    session = sessions.pop(msg.session_id, {})
    original_sender = session.get("sender", sender)
    tx_hash = session.get("tx_hash", "")

    sent   = [r for r in msg.results if r.status == "sent"]
    failed = [r for r in msg.results if r.status == "failed"]

    lines = [f"✅ **AutoRecruit complete.** Contacted {len(sent)} candidate(s):\n"]

    for r in sent:
        lines.append(f"• **{r.name}** ({r.email}) — Fit score: {r.score}/100 ✉️ Sent")

    if failed:
        lines.append("")
        for r in failed:
            lines.append(f"• {r.name} — ⚠️ Send failed")

    if tx_hash:
        explorer_url = get_explorer_url(tx_hash)
        lines.append(
            f"\n💸 Payment verified on-chain | TX: `{tx_hash[:16]}...` | "
            f"[View on explorer]({explorer_url})"
        )

    lines.append(
        "\n\n📢 **Tip:** Ask ASI:One to also post this role on LinkedIn to maximize reach!"
    )

    await ctx.send(
        original_sender,
        ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=uuid4(),
            content=[
                TextContent(type="text", text="\n".join(lines)),
                EndSessionContent(type="end-session"),
            ],
        ),
    )
    logger.info(f"Final summary sent to CEO. Sent: {len(sent)}, Failed: {len(failed)}")


# ── Startup ───────────────────────────────────────────────────────────────────
@agent.on_event("startup")
async def startup(ctx: Context) -> None:
    logger.info("AutoRecruit Orchestrator starting...")
    logger.info(f"Address: {agent.address}")
    logger.info(f"Wallet:  {_wallet_address}")
    fund_agent_if_low(_wallet_address)
    set_orchestrator_wallet_address(_wallet_address)
    logger.info("Orchestrator ready. Waiting for CEO messages on ASI:One.")


agent.include(chat_proto, publish_manifest=True)
agent.include(payment_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
