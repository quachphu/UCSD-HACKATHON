import os
import logging
from dotenv import load_dotenv
load_dotenv()

from uagents import Agent, Context
from openai import OpenAI
from agents.models.shared_models import RecruitmentRequest, TalentSearchRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCOUT_ADDRESS = os.getenv("SCOUT_ADDRESS", "")

agent = Agent(
    name="autorecruit-recruiter",
    seed=os.getenv("RECRUITER_SEED", "autorecruit-recruiter-diamond-2026-phu"),
    port=8002,
    mailbox=True,
    network="testnet",
)

asi_client = OpenAI(
    base_url="https://api.asi1.ai/v1",
    api_key=os.getenv("ASI1_API_KEY"),
)


@agent.on_event("startup")
async def startup(ctx: Context):
    logger.info(f"Recruiter Agent ready | address: {agent.address}")


@agent.on_message(model=RecruitmentRequest)
async def handle_recruitment_request(ctx: Context, sender: str, msg: RecruitmentRequest):
    logger.info(f"Recruiter: generating JD for '{msg.role}' in {msg.location} (count: {msg.count})")

    try:
        response = asi_client.chat.completions.create(
            model="asi1-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Write a concise, compelling job description for a {msg.role} role "
                    f"at a fast-growing AI startup in {msg.location}. "
                    f"Urgency: {msg.urgency}. Hiring {msg.count} people. "
                    "Include:\n"
                    "- 3 key responsibilities as bullet points\n"
                    "- Required skills: Python, ML/AI, LLMs, agent systems\n"
                    "- One sentence about startup culture\n"
                    "Keep it under 200 words. Direct, exciting, no corporate fluff."
                )
            }],
            max_tokens=400,
        )
        jd = response.choices[0].message.content.strip()
        logger.info(f"Recruiter: JD generated ({len(jd)} chars)")
    except Exception as e:
        logger.error(f"JD generation failed: {e}")
        jd = (
            f"We are hiring a {msg.role} in {msg.location}. "
            "Strong Python, ML, and LLM skills required. "
            "Fast-paced startup environment. Immediate start."
        )

    await ctx.send(
        SCOUT_ADDRESS,
        TalentSearchRequest(
            jd=jd,
            role=msg.role,
            location=msg.location,
            count=msg.count,
            session_id=msg.session_id,
        ),
    )
    logger.info(f"TalentSearchRequest sent to Scout | session: {msg.session_id}")


if __name__ == "__main__":
    agent.run()
