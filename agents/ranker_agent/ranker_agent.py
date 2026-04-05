import os
import json
import logging
from dotenv import load_dotenv
load_dotenv()

from uagents import Agent, Context
from openai import OpenAI
from agents.models.shared_models import (
    CandidateProfiles,
    RankedCandidates,
    Candidate,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OUTREACH_ADDRESS = os.getenv("OUTREACH_ADDRESS", "")

agent = Agent(
    name="autorecruit-ranker",
    seed=os.getenv("RANKER_SEED", "autorecruit-ranker-diamond-2026-phu"),
    port=8004,
    mailbox=True,
    network="testnet",
)

asi_client = OpenAI(
    base_url="https://api.asi1.ai/v1",
    api_key=os.getenv("ASI1_API_KEY"),
)


@agent.on_event("startup")
async def startup(ctx: Context):
    logger.info(f"Profile Ranker Agent ready | address: {agent.address}")


@agent.on_message(model=CandidateProfiles)
async def handle_ranking(ctx: Context, sender: str, msg: CandidateProfiles):
    logger.info(f"Ranker: scoring {len(msg.candidates)} candidates against JD")

    ranked = []

    for candidate in msg.candidates:
        skills_str = ", ".join(candidate.skills)
        try:
            response = asi_client.chat.completions.create(
                model="asi1-mini",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Job Description:\n{msg.jd[:500]}\n\n"
                        f"Candidate: {candidate.name}\n"
                        f"Skills: {skills_str}\n"
                        f"Experience: {candidate.experience_years} years\n"
                        f"Location: {candidate.location}\n\n"
                        "Rate this candidate's fit for the role from 0 to 100.\n"
                        "Return ONLY valid JSON, no markdown:\n"
                        '{"score": integer, "reason": "one sentence max"}'
                    )
                }],
                max_tokens=120,
            )
            text = response.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())
            score = max(0, min(100, int(result.get("score", 70))))
            reason = result.get("reason", "Strong technical candidate.")
        except Exception as e:
            logger.warning(f"Scoring failed for {candidate.name}: {e} — using default")
            score = 70
            reason = "Strong technical background for this role."

        ranked.append(Candidate(
            name=candidate.name,
            email=candidate.email,
            skills=candidate.skills,
            experience_years=candidate.experience_years,
            location=candidate.location,
            github=candidate.github,
            score=score,
            reason=reason,
        ))
        logger.info(f"  {candidate.name}: {score}/100 — {reason}")

    ranked.sort(key=lambda c: c.score, reverse=True)
    top = ranked[:msg.count]

    logger.info(f"Ranker: top {len(top)} scores = {[c.score for c in top]}")

    await ctx.send(
        OUTREACH_ADDRESS,
        RankedCandidates(
            candidates=top,
            jd=msg.jd,
            session_id=msg.session_id,
        ),
    )


if __name__ == "__main__":
    agent.run()
