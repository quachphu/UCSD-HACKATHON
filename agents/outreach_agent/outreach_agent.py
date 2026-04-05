import os
import logging
from dotenv import load_dotenv
load_dotenv()

from uagents import Agent, Context
from openai import OpenAI
import sendgrid
from sendgrid.helpers.mail import Mail

from agents.models.shared_models import (
    RankedCandidates,
    OutreachSummary,
    OutreachResult,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ORCHESTRATOR_ADDRESS = os.getenv("ORCHESTRATOR_ADDRESS", "")
SENDGRID_API_KEY     = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL           = os.getenv("FROM_EMAIL", "quachphuwork@gmail.com")
FROM_NAME            = "AutoRecruit AI"

agent = Agent(
    name="autorecruit-outreach",
    seed=os.getenv("OUTREACH_SEED", "autorecruit-outreach-diamond-2026-phu"),
    port=8005,
    mailbox=True,
    network="testnet",
)

asi_client = OpenAI(
    base_url="https://api.asi1.ai/v1",
    api_key=os.getenv("ASI1_API_KEY"),
)


def draft_personalized_email(candidate, jd: str) -> tuple:
    """Returns (subject, body) using ASI:One for personalization."""
    skills_str = ", ".join(candidate.skills[:3])
    try:
        response = asi_client.chat.completions.create(
            model="asi1-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Write a warm, concise cold recruiting email to {candidate.name}.\n"
                    f"Their background: {skills_str}, {candidate.experience_years} years experience.\n"
                    f"Why they fit: {candidate.reason}\n\n"
                    f"Role context: {jd[:250]}\n\n"
                    "Email structure (3 short paragraphs):\n"
                    "1. Personalized hook mentioning their specific skills\n"
                    "2. The opportunity — urgent AI project in Los Angeles, exciting mission\n"
                    "3. Simple CTA — 20-minute call this week\n"
                    "Sign off: AutoRecruit AI (Powered by Fetch.ai Agents)\n"
                    "Total: under 150 words. Genuine, no buzzwords."
                )
            }],
            max_tokens=300,
        )
        body = response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Email drafting failed for {candidate.name}: {e} — using template")
        body = (
            f"Hi {candidate.name},\n\n"
            f"We came across your profile and were impressed by your background in {skills_str}. "
            f"We have an urgent AI Engineer opening in Los Angeles and believe you'd be a great fit.\n\n"
            f"Would you have 20 minutes this week for a quick call?\n\n"
            f"Best,\nAutoRecruit AI (Powered by Fetch.ai Agents)"
        )

    subject = f"AI Engineer Opportunity — Los Angeles | {candidate.score}/100 Fit Match"
    return subject, body


@agent.on_event("startup")
async def startup(ctx: Context):
    logger.info(f"Outreach Agent ready | address: {agent.address}")
    logger.info(f"Sending from: {FROM_EMAIL}")


@agent.on_message(model=RankedCandidates)
async def handle_outreach(ctx: Context, sender: str, msg: RankedCandidates):
    logger.info(f"Outreach: preparing emails for {len(msg.candidates)} candidates")

    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    results = []

    for candidate in msg.candidates:
        logger.info(f"  Drafting email for {candidate.name} ({candidate.email})")
        subject, body = draft_personalized_email(candidate, msg.jd)

        try:
            mail = Mail(
                from_email=(FROM_EMAIL, FROM_NAME),
                to_emails=candidate.email,
                subject=subject,
                plain_text_content=body,
            )
            response = sg.client.mail.send.post(request_body=mail.get())
            sendgrid_id = response.headers.get("X-Message-Id", "")
            status_code = response.status_code

            if status_code == 202:
                logger.info(f"  ✓ Sent to {candidate.email} (HTTP {status_code})")
                results.append(OutreachResult(
                    name=candidate.name,
                    email=candidate.email,
                    score=candidate.score,
                    status="sent",
                    sendgrid_id=sendgrid_id,
                ))
            else:
                logger.warning(f"  ✗ Unexpected status {status_code} for {candidate.email}")
                results.append(OutreachResult(
                    name=candidate.name,
                    email=candidate.email,
                    score=candidate.score,
                    status="failed",
                ))

        except Exception as e:
            logger.error(f"  ✗ SendGrid error for {candidate.email}: {e}")
            results.append(OutreachResult(
                name=candidate.name,
                email=candidate.email,
                score=candidate.score,
                status="failed",
            ))

    sent_count = sum(1 for r in results if r.status == "sent")
    logger.info(f"Outreach complete: {sent_count}/{len(results)} emails sent")

    await ctx.send(
        ORCHESTRATOR_ADDRESS,
        OutreachSummary(
            results=results,
            session_id=msg.session_id,
        ),
    )


if __name__ == "__main__":
    agent.run()
