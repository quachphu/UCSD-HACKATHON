import os
import logging
from dotenv import load_dotenv
load_dotenv()

from uagents import Agent, Context
from agents.models.shared_models import (
    TalentSearchRequest,
    CandidateProfiles,
    Candidate,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RANKER_ADDRESS = os.getenv("RANKER_ADDRESS", "")

agent = Agent(
    name="autorecruit-scout",
    seed=os.getenv("SCOUT_SEED", "autorecruit-scout-diamond-2026-phu"),
    port=8003,
    mailbox=True,
    network="testnet",
)

# ── Demo candidate pool ───────────────────────────────────────────────────────
DEMO_CANDIDATES = [
    Candidate(
        name="Kshipra Dhame",
        email="kshipradhame.kd@gmail.com",
        skills=["Python", "LLMs", "uAgents", "Fetch.ai", "Multi-agent systems", "FastAPI"],
        experience_years=3,
        github="kshipra-fetch",
        location="Los Angeles, CA",
    ),
    Candidate(
        name="Rajashekar Vennavelli",
        email="rajashekar.vennavelli@fetch.ai",
        skills=["Machine Learning", "Agent systems", "FastAPI", "Python", "RAG", "NLP"],
        experience_years=4,
        github="rajashekarcs2023",
        location="Berkeley, CA",
    ),
    Candidate(
        name="Phu Quach",
        email="quachphuwork@gmail.com",
        skills=["ML", "uAgents", "FastAPI", "CGEV research", "Multi-agent AI", "Python"],
        experience_years=2,
        github="quachphu",
        location="Irvine, CA",
    ),
    Candidate(
        name="Thien Phu Quach",
        email="thienphu.quach01@student.csulb.edu",
        skills=["Python", "AI research", "Machine Learning", "Data Science"],
        experience_years=1,
        location="Long Beach, CA",
    ),
]


@agent.on_event("startup")
async def startup(ctx: Context):
    logger.info(f"Talent Scout Agent ready | address: {agent.address}")
    logger.info(f"Demo pool: {len(DEMO_CANDIDATES)} candidates loaded")


@agent.on_message(model=TalentSearchRequest)
async def handle_talent_search(ctx: Context, sender: str, msg: TalentSearchRequest):
    logger.info(f"Scout: searching for {msg.role} in {msg.location}")
    logger.info(f"Scout: returning {len(DEMO_CANDIDATES)} candidates to Ranker")

    await ctx.send(
        RANKER_ADDRESS,
        CandidateProfiles(
            candidates=DEMO_CANDIDATES,
            jd=msg.jd,
            count=msg.count,
            session_id=msg.session_id,
        ),
    )


if __name__ == "__main__":
    agent.run()
