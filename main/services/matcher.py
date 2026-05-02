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


# Shared skill vocabulary for ad-hoc extraction when a JD has no structured
# required_skills (vocabulary miss or unusual JD format).  Kept in sync with
# the parser skill lists so the same terms are recognised everywhere.
_SKILL_VOCAB: list[str] = [
    # Languages
    'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'ruby', 'go', 'rust',
    'kotlin', 'swift', 'php',
    # Web / frontend
    'html', 'css', 'react', 'angular', 'vue', 'node.js', 'express', 'flutter',
    # Backend / frameworks
    'django', 'flask', 'fastapi', 'graphql', 'rest api', 'microservices',
    # Databases
    'sql', 'mongodb', 'postgresql', 'mysql', 'redis', 'elasticsearch',
    # Cloud & DevOps
    'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'jenkins', 'terraform', 'linux', 'git',
    # Data / AI
    'machine learning', 'deep learning', 'ai', 'data science', 'data analysis',
    'tableau', 'power bi', 'excel', 'pandas', 'numpy', 'tensorflow', 'pytorch',
    # Methodologies
    'agile', 'scrum', 'devops',
    # IT / CS domain — broader terms that appear in IT-focused JDs and resumes
    'programming', 'software development', 'information technology', 'computer science',
    'networking', 'computer hardware', 'database management', 'systems analysis',
    # Python-specific skills — Python instructor and developer roles
    'object-oriented programming', 'oop', 'unit testing', 'debugging',
    'virtual environment', 'web development', 'backend development', 'full stack development',
    # Cybersecurity domain — for cybersecurity faculty and specialist roles
    'cybersecurity', 'network security', 'information security', 'ethical hacking',
    'penetration testing', 'cryptography', 'security operations', 'vulnerability assessment',
    # IT professional experience / qualification — CS/IT faculty and consultant roles
    'it consultancy', 'it certification', 'technical consulting', 'applied research',
    'e-learning', 'lms',
    # Soft skills
    'communication', 'teamwork', 'leadership', 'problem solving', 'critical thinking',
    'time management', 'adaptability', 'creativity', 'collaboration', 'decision making',
    # Academic / teaching domain — keep in sync with job_parser._load_soft_skills() and resume_parser._load_common_skills()
    'teaching', 'instruction', 'curriculum development', 'course design',
    'research', 'lecturing', 'academic writing', 'mentoring',
    'laboratory supervision', 'thesis supervision',
    # Pedagogical / instructional skills — key differentiators for teaching roles
    'lesson planning', 'classroom management', 'student engagement',
    'instructional design', 'course facilitation', 'learning management', 'student discipline',
    # Faculty / academic professional skills — CS/IT Faculty JD requirements
    'student assessment', 'student evaluation', 'academic advising', 'career guidance',
    'seminar facilitation', 'laboratory instruction',
    'professional development', 'faculty development', 'online learning', 'online course development',
    # Technology in education — Cybersecurity/CS Faculty JD requirements
    'educational technology', 'ai integration', 'digital transformation',
    'blended learning', 'technology-enhanced learning', 'interpersonal skills',
    # Instructor coaching skills — Python Programming Instructor JD requirements
    'coaching', 'constructive feedback', 'presentation skills',
    'project-based learning', 'student reporting',
]


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
            'junior': 1,
            'mid level': 2,
            'senior': 3,
            'lead': 4,
            'principal': 5,
            'manager': 4,
            'director': 5,
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
        # If the JD parser found no structured skills (vocabulary miss or unusual
        # JD format), extract them ad-hoc from the raw text using the shared
        # vocabulary so the skill component is never a free 1.0 for everyone.
        effective_required = list(job.required_skills or [])
        effective_preferred = list(job.preferred_skills or [])
        if not effective_required and not effective_preferred:
            effective_required = self._extract_vocab_skills(job.raw_text or '')

        skill_score, matching_skills, missing_skills = self.calculate_skill_match(
            resume.skills,
            effective_required,
            effective_preferred,
        )
        experience_score = self.calculate_experience_match(resume.experience, job.experience_level)
        education_score = self.calculate_education_match(resume.education, job.education_requirements)
        # Build a compact text of just the structured entities for keyword similarity.
        # Focusing on entities (skills, experience, education) instead of raw paragraphs
        # gives a cleaner signal; raw text is used as fallback when entities are empty.
        resume_entity_text = self._build_entity_text(resume.skills, resume.experience, resume.education)
        job_entity_text = self._build_entity_text(
            list(job.required_skills or []) + list(job.preferred_skills or []),
            [],
            job.education_requirements,
        )
        keyword_score = self.calculate_keyword_similarity(
            resume_entity_text or resume.raw_text,   # Prefer entity text; fall back to full resume text
            job_entity_text or job.raw_text,          # Prefer entity text; fall back to full JD text
        )
        location_match = self.check_location_match(resume.locations, job.location)
        remote_match = self.check_remote_match(resume.raw_text, job.remote_option)
        title_prediction_score = self.calculate_job_title_prediction_score(resume.raw_text, job.title)

        breakdown = {
            'skill_score': skill_score,
            'experience_score': experience_score,
            'education_score': education_score,
            'keyword_score': keyword_score,
            'location_score': 1.0 if location_match else 0.0,
            'remote_score': 1.0 if remote_match else 0.5,  # 0.5 (not 0.0) so remote mismatch is a soft penalty
        }
        # Two weight sets depending on whether the spaCy textcat model is available.
        # To adjust weights, edit the multipliers in each branch.
        # The two sets must sum to 1.0.
        if title_prediction_score is None:
            # spaCy textcat unavailable or returned None: use 6-component weights
            overall_score = (
                breakdown['skill_score'] * 0.4
                + breakdown['experience_score'] * 0.2
                + breakdown['education_score'] * 0.15
                + breakdown['keyword_score'] * 0.15
                + breakdown['location_score'] * 0.05
                + breakdown['remote_score'] * 0.05
            )
        else:
            # spaCy textcat returned a score: add title_prediction (15%) and reduce other weights
            breakdown['title_prediction_score'] = title_prediction_score
            overall_score = (
                breakdown['skill_score'] * 0.35
                + breakdown['experience_score'] * 0.18
                + breakdown['education_score'] * 0.12
                + breakdown['keyword_score'] * 0.10
                + breakdown['location_score'] * 0.05
                + breakdown['remote_score'] * 0.05
                + breakdown['title_prediction_score'] * 0.15
            )

        # Graduated skill ceiling: prevents experience, education, and keyword
        # components from padding a score when the candidate matches few required skills.
        #
        # Formula: ceiling = max(0.30, skill_score × 0.6 + 0.25)
        #   skill_score = 0.00 → ceiling = 0.30  (same as old hard floor)
        #   skill_score = 0.25 → ceiling = 0.40
        #   skill_score = 0.30 → ceiling = 0.43
        #   skill_score = 0.40 → ceiling = 0.49
        #   skill_score ≥ 0.50 → no ceiling applied (half or more skills matched)
        #
        # Example: a developer with Python/Java applying for a CS Professor role
        # that also requires teaching/curriculum/research would score ~0.28 on
        # skill_match → ceiling ~0.42, preventing the 81% score caused by
        # free experience + education + keyword padding.
        if skill_score < 0.5 and (effective_required or effective_preferred):
            skill_ceiling = max(0.30, skill_score * 0.6 + 0.25)
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
        resume_list = list(resume_skills or [])
        required = list(job_required_skills or [])
        preferred = list(job_preferred_skills or [])

        if not required and not preferred:
            return 1.0, [], []

        model = get_embedding_model()

        if model and resume_list:
            resume_embeddings = model.encode(resume_list, convert_to_tensor=True)

            def semantic_best_score(skill: str, candidate_embeddings) -> float:
                skill_embedding = model.encode([skill], convert_to_tensor=True)
                scores = st_util.cos_sim(skill_embedding, candidate_embeddings)[0]
                return float(scores.max())

            # Raised from 0.65 → 0.72 → 0.78: stricter threshold prevents cross-domain
            # matches (e.g. 'teaching' vs 'python', or 'C++' vs 'Java') from inflating
            # skill scores and producing misleadingly high overall match percentages.
            threshold = 0.78
            required_scores = [semantic_best_score(skill, resume_embeddings) for skill in required] if required else []
            preferred_scores = [semantic_best_score(skill, resume_embeddings) for skill in preferred] if preferred else []

            required_matches = [skill for skill, score in zip(required, required_scores) if score >= threshold]
            missing_skills = [skill for skill, score in zip(required, required_scores) if score < threshold]
            preferred_matches = [skill for skill, score in zip(preferred, preferred_scores) if score >= threshold]

            # Clamp sub-threshold scores to 0.0 before averaging so that
            # semantically close but wrong skills (score 0.55–0.71) do not
            # contribute partial credit.  Without clamping, a JS developer
            # against a Python requirement would still raise the average.
            required_scores_avg = [s if s >= threshold else 0.0 for s in required_scores]
            preferred_scores_avg = [s if s >= threshold else 0.0 for s in preferred_scores]

            required_score = (sum(required_scores_avg) / len(required_scores_avg)) if required_scores_avg else 1.0
            # Only compute preferred_score when there are actual preferred skills to score.
            # Previously defaulted to 1.0 when preferred=[],  giving a free +20% to every resume.
            preferred_score = (sum(preferred_scores_avg) / len(preferred_scores_avg)) if preferred_scores_avg else None
        else:
            resume_normalized = set(self._normalize_text(skill) for skill in resume_list)
            required_normalized = [self._normalize_text(skill) for skill in required]
            preferred_normalized = [self._normalize_text(skill) for skill in preferred]

            required_matches = [skill for skill, normalized in zip(required, required_normalized) if normalized in resume_normalized]
            missing_skills = [skill for skill, normalized in zip(required, required_normalized) if normalized not in resume_normalized]
            preferred_matches = [skill for skill, normalized in zip(preferred, preferred_normalized) if normalized in resume_normalized]

            required_score = len(required_matches) / len(required) if required else 1.0
            # Same fix for the fallback branch: no preferred skills → no preferred bonus.
            preferred_score = len(preferred_matches) / len(preferred) if preferred else None

        # When preferred skills exist, blend required (80%) and preferred (20%).
        # When they don't, the score is purely the required skill match — no free bonus.
        if preferred_score is not None:
            score = (required_score * 0.8) + (preferred_score * 0.2)
        else:
            score = required_score
        return score, sorted(set(required_matches + preferred_matches)), sorted(missing_skills)

    def calculate_experience_match(self, resume_experience: Iterable[str], job_experience_level: Optional[str]) -> float:
        if not job_experience_level:
            return 1.0  # No experience requirement stated → full score for everyone
        resume_score = self._estimate_experience_level(resume_experience)
        # If the job level label isn't in experience_mapping, default to 2 (mid-level)
        job_score = self.experience_mapping.get(job_experience_level.lower(), 2)
        if resume_score >= job_score:
            return 1.0  # Meets or exceeds the requirement
        return resume_score / max(job_score, 1)  # Partial credit proportional to how close they are

    def calculate_education_match(self, resume_education: Iterable[str], education_requirements: Iterable[str]) -> float:
        if not education_requirements:
            return 1.0
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
            return True
        location = job_location.lower()
        if any(token in location for token in ('remote', 'hybrid', 'anywhere')):
            return True
        return any(item.lower() in location or location in item.lower() for item in resume_locations)

    def check_remote_match(self, resume_text: str, job_remote_option: bool) -> bool:
        if not job_remote_option:
            return True
        text = resume_text.lower()
        return any(token in text for token in ('remote', 'work from home', 'hybrid', 'distributed'))

    def calculate_job_title_prediction_score(self, resume_text: str, job_title: Optional[str]) -> Optional[float]:
        if not resume_text.strip() or not job_title:
            return None  # Returns None → matcher uses the 6-component weight set (no textcat)
        classifier = get_job_title_classifier()
        score = classifier.score_label(resume_text, job_title)
        if score is None:
            return None  # Model not loaded or label not recognised → textcat weight set skipped
        return max(0.0, min(1.0, float(score)))  # Clamp to [0, 1] to guard against model edge cases

    def _extract_vocab_skills(self, text: str) -> list[str]:
        """Extract skills from free text using the shared vocabulary.
        Used as a fallback when structured required_skills are empty."""
        text_lower = text.lower()
        return [
            skill.title()
            for skill in _SKILL_VOCAB
            if re.search(r'\b' + re.escape(skill) + r'\b', text_lower)
        ]

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