from uagents import Model
from typing import List, Optional


class RecruitmentRequest(Model):
    role: str
    count: int
    location: str
    urgency: str
    session_id: str


class TalentSearchRequest(Model):
    jd: str
    role: str
    location: str
    count: int
    session_id: str


class Candidate(Model):
    name: str
    email: str
    skills: List[str]
    experience_years: int
    location: str
    github: Optional[str] = ""
    score: Optional[int] = 0
    reason: Optional[str] = ""


class CandidateProfiles(Model):
    candidates: List[Candidate]
    jd: str
    count: int
    session_id: str


class RankedCandidates(Model):
    candidates: List[Candidate]
    jd: str
    session_id: str


class OutreachResult(Model):
    name: str
    email: str
    score: int
    status: str
    sendgrid_id: Optional[str] = ""


class OutreachSummary(Model):
    results: List[OutreachResult]
    session_id: str
