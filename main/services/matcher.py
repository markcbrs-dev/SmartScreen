"""
main/services/matcher.py — Multi-factor resume-to-job scoring engine.

OVERVIEW
--------
ResumeJobMatcher.match_resume_to_job() is the core algorithm.
It produces a JobMatchResult with an overall_score (0.0–1.0) and a breakdown.

SCORING WEIGHTS (when spaCy textcat is NOT available)
------------------------------------------------------
  Skill match       40%  — keyword/semantic overlap of resume skills vs. JD skills
  Experience match  20%  — estimated experience level vs. JD requirement
  Education match   15%  — estimated education level vs. JD requirement
  Keyword similarity15%  — TF-IDF cosine (sklearn) or semantic (sentence-transformers)
  Location match     5%  — resume location vs. JD location
  Remote match       5%  — remote preference detection

SCORING WEIGHTS (when spaCy textcat IS available, adds title prediction score)
-------------------------------------------------------------------------------
  Skill match       35%
  Experience match  18%
  Education match   12%
  Keyword similarity10%
  Title prediction  15%  ← spaCy textcat model (models/spacy_job_classifier/)
  Location match     5%
  Remote match       5%

HOW TO MODIFY
-------------
  Adjust weights          → edit the `overall_score = (...)` expressions in
                              match_resume_to_job() (both the with/without textcat branches)
  Change skill vocabulary → edit _SKILL_VOCAB (used as fallback when JD has no skills)
                              Keep in sync with _load_common_skills() in resume_parser.py
  Change semantic threshold → edit `threshold = 0.72` in calculate_skill_match()
  Change experience mapping → edit self.experience_mapping in __init__()
  Change education mapping  → edit self.education_mapping in __init__()
  Change skill floor        → edit the `if skill_score == 0.0` block at the end of
                              match_resume_to_job()
  Add a new scoring factor  → compute it, add to breakdown dict, add its weight to
                              the overall_score sum, and add a field to JobMatchResult
                              in entities.py

DEBUG
-----
  Wrong overall score? → check breakdown dict in the returned JobMatchResult
  Skills not matching? → check _SKILL_VOCAB and _load_common_skills() in resume_parser.py
  NaN / 0 scores?      → check that resume_text / raw_text fields are not empty
"""

import re
from typing import Iterable, Optional

from .entities import JobMatchResult, JobProfile, ResumeProfile
from .skills import (
    canonicalize_skill_list,
    extract_canonical_skills,
    normalize_experience_level,
    normalize_location_text,
    split_skills_by_category,
)
from .spacy_job_classifier import get_job_title_classifier

try:
    from sentence_transformers import SentenceTransformer, util as st_util
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SentenceTransformer = None
    st_util = None
    SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None

_embedding_model = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None and SENTENCE_TRANSFORMERS_AVAILABLE:
        _embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _embedding_model


class ResumeJobMatcher:
    def __init__(self):
        self.vectorizer = None
        if TfidfVectorizer is not None:
            # ngram_range=(1,2) captures both single words and two-word phrases (e.g. 'machine learning')
            # max_features=5000 limits vocabulary size for performance; raise if similarity scores seem low
            self.vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2), max_features=5000)
        # Ordinal scale 1-5 used to compare resume experience level to job requirement
        # Edit the values here to change how seniority labels map to the scale
        self.experience_mapping = {
            'entry level': 1,
            'mid level': 2,
            'senior': 3,
            'lead': 4,
            'principal': 5,
            'manager': 4,
            'director': 5,
            'vp': 5,
            'c-level': 5,
        }
        # Ordinal scale 1-5 for education level comparison
        # Edit the values here to change how degree keywords map to the scale
        self.education_mapping = {
            'high school': 1,
            'associate': 2,
            'diploma': 2,
            'bachelor': 3,
            'master': 4,
            'mba': 4,
            'phd': 5,
            'doctorate': 5,
        }

    def match_resume_to_job(self, resume: ResumeProfile, job: JobProfile) -> JobMatchResult:
        # If the JD parser found no structured skills, extract canonical skills
        # ad-hoc from the raw text so the skill component has consistent aliases.
        effective_required = canonicalize_skill_list(job.required_skills or [])
        effective_preferred = canonicalize_skill_list(job.preferred_skills or [])
        if not effective_required and not effective_preferred:
            effective_required = self._extract_vocab_skills(job.raw_text or '')

        normalized_resume_skills = canonicalize_skill_list(resume.skills or [])
        normalized_job_location = normalize_location_text(job.location)
        normalized_experience_level = normalize_experience_level(job.experience_level)
        skill_known = bool(effective_required or effective_preferred)
        experience_known = bool(normalized_experience_level)
        education_known = bool(job.education_requirements)
        location_known = self._job_has_location_requirement(normalized_job_location)
        remote_known = self._job_has_work_mode_requirement(job.raw_text or '', job.remote_option)

        skill_score, matching_skills, missing_skills = self.calculate_skill_match(
            normalized_resume_skills,
            effective_required,
            effective_preferred,
        )
        experience_score = self.calculate_experience_match(resume.experience, normalized_experience_level)
        education_score = self.calculate_education_match(resume.education, job.education_requirements)
        # Build a compact text of just the structured entities for keyword similarity.
        # Focusing on entities (skills, experience, education) instead of raw paragraphs
        # gives a cleaner signal; raw text is used as fallback when entities are empty.
        resume_entity_text = self._build_entity_text(normalized_resume_skills, resume.experience, resume.education)
        job_entity_text = self._build_entity_text(
            effective_required + effective_preferred,
            [],
            job.education_requirements,
        )
        entity_keyword_score = self.calculate_keyword_similarity(
            resume_entity_text or resume.raw_text,
            job_entity_text or job.raw_text,
        )
        if resume_entity_text and job_entity_text and resume.raw_text.strip() and job.raw_text.strip():
            raw_keyword_score = self.calculate_keyword_similarity(resume.raw_text, job.raw_text)
            keyword_score = (entity_keyword_score * 0.8) + (raw_keyword_score * 0.2)
        else:
            keyword_score = entity_keyword_score
        location_match = self.check_location_match(resume.locations, normalized_job_location) if location_known else False
        remote_match = self.check_remote_match(resume.raw_text, job.raw_text or '', job.remote_option) if remote_known else False
        title_prediction_score = self.calculate_job_title_prediction_score(resume.raw_text, job.title)

        breakdown = {
            'skill_score': skill_score,
            'experience_score': experience_score,
            'education_score': education_score,
            'keyword_score': keyword_score,
        }
        if title_prediction_score is None:
            components = [
                ('skill_score', skill_score if skill_known else None, 0.4),
                ('experience_score', experience_score if experience_known else None, 0.2),
                ('education_score', education_score if education_known else None, 0.15),
                ('keyword_score', keyword_score, 0.15),
                ('location_score', 1.0 if location_match else 0.0 if location_known else None, 0.05),
                ('remote_score', 1.0 if remote_match else 0.0 if remote_known else None, 0.05),
            ]
        else:
            components = [
                ('skill_score', skill_score if skill_known else None, 0.35),
                ('experience_score', experience_score if experience_known else None, 0.18),
                ('education_score', education_score if education_known else None, 0.12),
                ('keyword_score', keyword_score, 0.10),
                ('location_score', 1.0 if location_match else 0.0 if location_known else None, 0.05),
                ('remote_score', 1.0 if remote_match else 0.0 if remote_known else None, 0.05),
                ('title_prediction_score', title_prediction_score, 0.15),
            ]

        overall_score = self._calculate_weighted_score(components, breakdown)

        # Graduated skill ceiling: prevents experience, education, and keyword
        # components from padding a score when the candidate matches only a
        # minority of the required skills.
        #
        # Formula: ceiling = max(0.20, skill_score × 0.5 + 0.15)
        #   skill_score = 0.00 → ceiling = 0.20
        #   skill_score = 0.20 → ceiling = 0.25
        #   skill_score = 0.30 → ceiling = 0.30
        #   skill_score = 0.40 → ceiling = 0.35
        #   skill_score ≥ 0.50 → no ceiling applied (half or more skills matched)
        #
        # This keeps one- or two-skill matches from landing in the mid-30s or
        # higher just because experience, education, or keyword similarity are strong.
        if skill_known and skill_score < 0.5 and (effective_required or effective_preferred):
            skill_ceiling = max(0.20, skill_score * 0.5 + 0.15)
            overall_score = min(overall_score, skill_ceiling)

        return JobMatchResult(
            overall_score=max(0.0, min(1.0, overall_score)),
            skill_match_score=skill_score,
            experience_match_score=experience_score,
            education_match_score=education_score,
            keyword_match_score=keyword_score,
            location_match=location_match,
            remote_match=remote_match,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            breakdown=breakdown,
        )

    def _calculate_weighted_score(
        self,
        components: list[tuple[str, Optional[float], float]],
        breakdown: dict[str, float],
    ) -> float:
        weighted_sum = 0.0
        active_weight = 0.0
        for name, score, weight in components:
            if score is None:
                continue
            breakdown[name] = score
            weighted_sum += score * weight
            active_weight += weight
        if active_weight == 0:
            return 0.0
        return weighted_sum / active_weight

    def _job_has_location_requirement(self, job_location: Optional[str]) -> bool:
        if not job_location:
            return False
        location = job_location.lower()
        return not any(token in location for token in ('remote', 'hybrid', 'anywhere'))

    def _job_has_work_mode_requirement(self, job_text: str, job_remote_option: bool) -> bool:
        if job_remote_option:
            return True
        text = (job_text or '').lower()
        return any(token in text for token in ('onsite', 'on-site', 'on site', 'hybrid', 'remote', 'work from home', 'wfh'))

    def _build_entity_text(
        self,
        skills: Iterable[str],
        experience: Iterable[str],
        education: Iterable[str],
    ) -> str:
        parts = []
        parts.extend(skills or [])
        parts.extend(experience or [])
        parts.extend(education or [])
        return ' '.join(str(part) for part in parts if part)

    def calculate_skill_match(
        self,
        resume_skills: Iterable[str],
        job_required_skills: Iterable[str],
        job_preferred_skills: Iterable[str],
    ) -> tuple[float, list[str], list[str]]:
        resume_list = canonicalize_skill_list(resume_skills or [])
        required = canonicalize_skill_list(job_required_skills or [])
        preferred = canonicalize_skill_list(job_preferred_skills or [])

        if not required and not preferred:
            return 0.5, [], []

        model = get_embedding_model()
        threshold = 0.78

        resume_hard, resume_soft = split_skills_by_category(resume_list)
        required_hard, required_soft = split_skills_by_category(required)
        preferred_hard, preferred_soft = split_skills_by_category(preferred)

        component_specs = [
            (resume_hard, required_hard, 0.70, True),
            (resume_hard, preferred_hard, 0.15, False),
            (resume_soft, required_soft, 0.10, True),
            (resume_soft, preferred_soft, 0.05, False),
        ]

        total_weight = 0.0
        weighted_score = 0.0
        matched_skills = set()
        missing_skills = []
        for resume_group, job_group, weight, track_missing in component_specs:
            group_score, group_matches, group_missing = self._score_skill_group(resume_group, job_group, model, threshold)
            if group_score is None:
                continue
            total_weight += weight
            weighted_score += group_score * weight
            matched_skills.update(group_matches)
            if track_missing:
                missing_skills.extend(group_missing)

        score = (weighted_score / total_weight) if total_weight else 0.5
        return score, sorted(matched_skills), sorted(set(missing_skills))

    def _score_skill_group(
        self,
        resume_skills: list[str],
        job_skills: list[str],
        model,
        threshold: float,
    ) -> tuple[Optional[float], list[str], list[str]]:
        if not job_skills:
            return None, [], []

        if model and resume_skills:
            resume_embeddings = model.encode(resume_skills, convert_to_tensor=True)

            def semantic_best_score(skill: str) -> float:
                skill_embedding = model.encode([skill], convert_to_tensor=True)
                scores = st_util.cos_sim(skill_embedding, resume_embeddings)[0]
                return float(scores.max())

            raw_scores = [semantic_best_score(skill) for skill in job_skills]
            scores = [score if score >= threshold else 0.0 for score in raw_scores]
            matches = [skill for skill, score in zip(job_skills, raw_scores) if score >= threshold]
        else:
            normalized_resume = set(self._normalize_text(skill) for skill in resume_skills)
            normalized_job = [self._normalize_text(skill) for skill in job_skills]
            scores = [1.0 if skill in normalized_resume else 0.0 for skill in normalized_job]
            matches = [skill for skill, normalized in zip(job_skills, normalized_job) if normalized in normalized_resume]

        missing = [skill for skill in job_skills if skill not in matches]
        return sum(scores) / len(scores), matches, missing

    def calculate_experience_match(self, resume_experience: Iterable[str], job_experience_level: Optional[str]) -> float:
        if not job_experience_level:
            return 0.5
        resume_score = self._estimate_experience_level(resume_experience)
        # If the job level label isn't in experience_mapping, default to 2 (mid-level)
        job_score = self.experience_mapping.get(normalize_experience_level(job_experience_level) or '', 2)
        if resume_score >= job_score:
            return 1.0  # Meets or exceeds the requirement
        return resume_score / max(job_score, 1)  # Partial credit proportional to how close they are

    def calculate_education_match(self, resume_education: Iterable[str], education_requirements: Iterable[str]) -> float:
        if not education_requirements:
            return 0.5
        # Resume defaults to 0: a resume with no education section should not be
        # assumed to have a bachelor's degree (the old default of 3 gave everyone
        # a free pass against bachelor-level requirements).
        # Job requirements default to 3 (bachelor's): if the text mentions education
        # requirements but none of the explicit keywords match, bachelor's is the
        # standard implicit expectation.
        resume_level = self._estimate_education_level(resume_education, default=0)
        job_level = self._estimate_education_level(education_requirements, default=3)
        if resume_level >= job_level:
            return 1.0
        return resume_level / max(job_level, 1)

    def calculate_keyword_similarity(self, resume_text: str, job_text: str) -> float:
        if not resume_text.strip() or not job_text.strip():
            return 0.0

        # Tier 1: sentence-transformers semantic embeddings (most accurate, but slowest)
        model = get_embedding_model()
        if model:
            resume_embedding = model.encode([resume_text], convert_to_tensor=True)
            job_embedding = model.encode([job_text], convert_to_tensor=True)
            score = st_util.cos_sim(resume_embedding, job_embedding)
            return float(score[0][0])

        # Tier 2: TF-IDF cosine similarity (faster, requires sklearn)
        if self.vectorizer is not None and cosine_similarity is not None:
            matrix = self.vectorizer.fit_transform([resume_text, job_text])
            similarity = cosine_similarity(matrix[0:1], matrix[1:2])
            return float(similarity[0][0])

        # Tier 3: plain word-overlap Jaccard fallback (no external dependency)
        resume_words = set(re.findall(r'\b\w+\b', resume_text.lower()))
        job_words = set(re.findall(r'\b\w+\b', job_text.lower()))
        if not job_words:
            return 0.0
        return len(resume_words & job_words) / len(job_words)

    def check_location_match(self, resume_locations: Iterable[str], job_location: Optional[str]) -> bool:
        if not job_location:
            return False
        location = job_location.lower()
        if any(token in location for token in ('remote', 'hybrid', 'anywhere')):
            return True
        return any(
            normalize_location_text(item).lower() in location or location in normalize_location_text(item).lower()
            for item in resume_locations
        )

    def check_remote_match(self, resume_text: str, job_text: str, job_remote_option: bool) -> bool:
        if job_remote_option:
            return True
        job_text = (job_text or '').lower()
        if not any(token in job_text for token in ('onsite', 'on-site', 'on site', 'hybrid', 'remote', 'work from home', 'wfh')):
            return False
        text = resume_text.lower()
        wants_remote = any(token in text for token in ('remote', 'work from home', 'hybrid', 'distributed'))
        requires_onsite = any(token in job_text for token in ('onsite', 'on-site', 'on site'))
        if requires_onsite and wants_remote:
            return False
        return True

    def calculate_job_title_prediction_score(self, resume_text: str, job_title: Optional[str]) -> Optional[float]:
        if not resume_text.strip() or not job_title:
            return None  # Returns None → matcher uses the 6-component weight set (no textcat)
        classifier = get_job_title_classifier()
        score = classifier.score_label(resume_text, job_title)
        if score is None:
            return None  # Model not loaded or label not recognised → textcat weight set skipped
        score = max(0.0, min(1.0, float(score)))
        if score < 0.45:
            return None
        return score

    def _extract_vocab_skills(self, text: str) -> list[str]:
        """Extract skills from free text using the shared vocabulary.
        Used as a fallback when structured required_skills are empty."""
        return extract_canonical_skills(text)

    def _normalize_text(self, text: str) -> str:
        return text.lower().strip()

    def _estimate_experience_level(self, experience_lines: Iterable[str]) -> int:
        best_score = 1  # Default to entry-level (1) if no experience lines are found
        year_pattern = re.compile(r'(\d+)\+?\s*(?:years?|yrs?)', re.IGNORECASE)

        for line in experience_lines:
            lower_line = line.lower()

            # Try to read explicit year counts first ('5 years of experience' → score 3)
            # Edit these thresholds if the mapping doesn't match your domain's expectations
            for match in year_pattern.findall(lower_line):
                years = int(match)
                if years <= 1:
                    best_score = max(best_score, 1)
                elif years <= 3:
                    best_score = max(best_score, 2)
                elif years <= 5:
                    best_score = max(best_score, 3)
                elif years <= 8:
                    best_score = max(best_score, 4)
                else:
                    best_score = max(best_score, 5)

            # Also match seniority label keywords ('senior developer' → score 3)
            for label, score in self.experience_mapping.items():
                if label in lower_line:
                    best_score = max(best_score, score)
        return best_score

    def _estimate_education_level(self, education_lines: Iterable[str], default: int = 0) -> int:
        best_score = default
        for line in education_lines:
            lower_line = line.lower()
            for label, score in self.education_mapping.items():
                if label in lower_line:
                    best_score = max(best_score, score)
        return best_score