from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ResumeProfile:
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    skills: List[str] = field(default_factory=list)
    education: List[str] = field(default_factory=list)
    experience: List[str] = field(default_factory=list)
    organizations: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    raw_text: str = ''


@dataclass
class JobProfile:
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[str] = None
    experience_level: Optional[str] = None
    salary_range: Optional[str] = None
    remote_option: bool = False
    required_skills: List[str] = field(default_factory=list)
    preferred_skills: List[str] = field(default_factory=list)
    responsibilities: List[str] = field(default_factory=list)
    qualifications: List[str] = field(default_factory=list)
    education_requirements: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    benefits: List[str] = field(default_factory=list)
    industries: List[str] = field(default_factory=list)
    raw_text: str = ''


@dataclass
class JobMatchResult:
    overall_score: float
    skill_match_score: float
    experience_match_score: float
    education_match_score: float
    keyword_match_score: float
    location_match: bool
    remote_match: bool
    matching_skills: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)
    breakdown: Dict[str, float] = field(default_factory=dict)