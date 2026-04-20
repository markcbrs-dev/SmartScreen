import re
from typing import Iterable, Optional

from .entities import JobMatchResult, JobProfile, ResumeProfile

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None


class ResumeJobMatcher:
    def __init__(self):
        self.vectorizer = None
        if TfidfVectorizer is not None:
            self.vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2), max_features=5000)
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
        skill_score, matching_skills, missing_skills = self.calculate_skill_match(
            resume.skills,
            job.required_skills,
            job.preferred_skills,
        )
        experience_score = self.calculate_experience_match(resume.experience, job.experience_level)
        education_score = self.calculate_education_match(resume.education, job.education_requirements)
        keyword_score = self.calculate_keyword_similarity(resume.raw_text, job.raw_text)
        location_match = self.check_location_match(resume.locations, job.location)
        remote_match = self.check_remote_match(resume.raw_text, job.remote_option)

        breakdown = {
            'skill_score': skill_score,
            'experience_score': experience_score,
            'education_score': education_score,
            'keyword_score': keyword_score,
            'location_score': 1.0 if location_match else 0.0,
            'remote_score': 1.0 if remote_match else 0.5,
        }
        overall_score = (
            breakdown['skill_score'] * 0.4
            + breakdown['experience_score'] * 0.2
            + breakdown['education_score'] * 0.15
            + breakdown['keyword_score'] * 0.15
            + breakdown['location_score'] * 0.05
            + breakdown['remote_score'] * 0.05
        )

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

    def calculate_skill_match(
        self,
        resume_skills: Iterable[str],
        job_required_skills: Iterable[str],
        job_preferred_skills: Iterable[str],
    ) -> tuple[float, list[str], list[str]]:
        resume_normalized = set(self.normalize_skills(resume_skills))
        required = set(self.normalize_skills(job_required_skills))
        preferred = set(self.normalize_skills(job_preferred_skills))

        if not required and not preferred:
            return 1.0, [], []

        required_matches = resume_normalized & required
        preferred_matches = resume_normalized & preferred
        required_score = len(required_matches) / len(required) if required else 1.0
        preferred_score = len(preferred_matches) / len(preferred) if preferred else 1.0
        score = (required_score * 0.8) + (preferred_score * 0.2)
        return score, sorted(required_matches | preferred_matches), sorted(required - resume_normalized)

    def calculate_experience_match(self, resume_experience: Iterable[str], job_experience_level: Optional[str]) -> float:
        if not job_experience_level:
            return 1.0
        resume_score = self._estimate_experience_level(resume_experience)
        job_score = self.experience_mapping.get(job_experience_level.lower(), 2)
        if resume_score >= job_score:
            return 1.0
        return resume_score / max(job_score, 1)

    def calculate_education_match(self, resume_education: Iterable[str], education_requirements: Iterable[str]) -> float:
        if not education_requirements:
            return 1.0
        resume_level = self._estimate_education_level(resume_education)
        job_level = self._estimate_education_level(education_requirements)
        if resume_level >= job_level:
            return 1.0
        return resume_level / max(job_level, 1)

    def calculate_keyword_similarity(self, resume_text: str, job_text: str) -> float:
        if not resume_text.strip() or not job_text.strip():
            return 0.0
        if self.vectorizer is not None and cosine_similarity is not None:
            matrix = self.vectorizer.fit_transform([resume_text, job_text])
            similarity = cosine_similarity(matrix[0:1], matrix[1:2])
            return float(similarity[0][0])
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

    def normalize_skills(self, skills: Iterable[str]) -> list[str]:
        normalized = []
        aliases = {
            'python': {'python', 'python 3', 'python3', 'py'},
            'javascript': {'javascript', 'js'},
            'typescript': {'typescript', 'ts'},
            'node.js': {'node', 'node.js', 'nodejs'},
            'aws': {'aws', 'amazon web services'},
            'react': {'react', 'react.js', 'reactjs'},
            'sql': {'sql', 'mysql', 'postgresql', 'database'},
        }
        for skill in skills:
            if not skill:
                continue
            candidate = skill.lower().strip()
            mapped = None
            for canonical, values in aliases.items():
                if candidate in values or any(value in candidate for value in values):
                    mapped = canonical.title()
                    break
            normalized.append(mapped or skill.title())
        return sorted(set(normalized))

    def _estimate_experience_level(self, experience_lines: Iterable[str]) -> int:
        best_score = 2
        for line in experience_lines:
            lower_line = line.lower()
            for label, score in self.experience_mapping.items():
                if label in lower_line:
                    best_score = max(best_score, score)
        return best_score

    def _estimate_education_level(self, education_lines: Iterable[str]) -> int:
        best_score = 3
        for line in education_lines:
            lower_line = line.lower()
            for label, score in self.education_mapping.items():
                if label in lower_line:
                    best_score = max(best_score, score)
        return best_score