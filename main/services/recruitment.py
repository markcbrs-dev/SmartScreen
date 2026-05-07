"""
main/services/recruitment.py — Orchestration layer; the single entry point for all
resume/job parsing and match-scoring operations used by views.py.

OVERVIEW
--------
This module wires together the parsers (resume_parser, job_parser) and the scorer
(matcher) so that views never call those directly. Cached singleton instances keep
the NLP models loaded in memory for the lifetime of the process.

HOW TO MODIFY
-------------
  Change how a job description is saved    → edit replace_active_job_description()
  Change how match scores are computed     → see matcher.py; the entry point here is
                                              refresh_applicant_match()
  Add a new parsed field to Applicant      → update update_applicant_from_resume_profile()
                                              and build_resume_profile_from_applicant()
  Add a new parsed field to JobDescription → update update_job_from_profile() and
                                              build_job_profile_from_model()
  Change how multiple jobs are scored      → edit evaluate_best_job_match()
  Change what match_notes stores           → edit serialize_match_result()
  Clear parser/matcher caches (e.g. tests) → call get_resume_parser.cache_clear(),
                                              get_job_parser.cache_clear(), or
                                              get_matcher.cache_clear()

FUNCTIONS IN THIS FILE
----------------------
  parse_resume_upload             — parse a resume file-like object → ResumeProfile
  parse_job_upload                — parse a JD file-like object → JobProfile
  extract_job_display_text        — get formatted display text for a JD file
  extract_job_description_section — extract the description body section only
  extract_description_preview_text— extract a short preview for the apply page
  extract_about_job_onward_text   — text from the first job/description heading onward
  get_active_job_description      — fetch the most recently uploaded job (or None)
  get_active_job_descriptions     — fetch all jobs (ordered newest first)
  get_job_description_by_id       — fetch a specific job by PK
  replace_active_job_description  — parse + save a new job description upload
  remove_active_job_description   — delete a job and its file
  update_applicant_from_resume_profile — copy parsed fields onto an Applicant instance
  update_job_from_profile         — copy parsed fields onto a JobDescription instance
  build_resume_profile_from_applicant  — build a ResumeProfile from a saved Applicant
  build_job_profile_from_model    — build a JobProfile from a saved JobDescription
  refresh_applicant_match         — re-score one applicant against the active job
  refresh_applicant_matches       — re-score all applicants (batch)
  evaluate_best_job_match         — score a resume against multiple jobs, pick best
  serialize_match_result          — convert JobMatchResult → JSON-safe dict
"""

import os
from functools import lru_cache

from django.core.files.base import ContentFile, File

from main.models import JobDescription

from .entities import JobProfile, ResumeProfile
from .job_parser import JobDescriptionParser
from .matcher import ResumeJobMatcher
from .resume_parser import ResumeParser


# -----------------------------------------------------------------------
# SINGLETON PARSERS & MATCHER
# These are instantiated once per process via lru_cache.
# If NLP models are slow to load, that cost is paid once at first use.
# To force a reload (e.g. during tests), call .cache_clear() on each.
# -----------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_resume_parser() -> ResumeParser:
    return ResumeParser()


@lru_cache(maxsize=1)
def get_job_parser() -> JobDescriptionParser:
    return JobDescriptionParser()


@lru_cache(maxsize=1)
def get_matcher() -> ResumeJobMatcher:
    return ResumeJobMatcher()


def parse_resume_upload(file_obj) -> ResumeProfile:
    return get_resume_parser().parse(file_obj, filename=getattr(file_obj, 'name', None))


def parse_job_upload(file_obj) -> JobProfile | list[JobProfile]:
    return get_job_parser().parse(file_obj, filename=getattr(file_obj, 'name', None))


def extract_job_display_text(file_obj, filename: str | None = None) -> str:
    return get_job_parser().extract_display_text(file_obj, filename=filename or getattr(file_obj, 'name', None))


def extract_job_description_section(raw_text: str) -> str:
    return get_job_parser().extract_job_description_section(raw_text or '')


def extract_description_preview_text(raw_text: str) -> str:
    return get_job_parser().extract_description_preview_text(raw_text or '')


def extract_about_job_onward_text(raw_text: str) -> str:
    return get_job_parser().extract_about_job_onward_text(raw_text or '')


# -----------------------------------------------------------------------
# JOB DESCRIPTION CRUD
# replace_active_job_description() is called by the dashboard view on upload.
# remove_active_job_description() is called by the dashboard view on remove.
# -----------------------------------------------------------------------
def get_active_job_description() -> JobDescription | None:
    return JobDescription.objects.order_by('-created_at', '-id').first()


def get_job_description_by_id(job_id) -> JobDescription | None:
    if not job_id:
        return None
    return JobDescription.objects.filter(id=job_id).first()


def _delete_job_description(job: JobDescription) -> None:
    if job.description_file:
        job.description_file.delete(save=False)
    job.delete()


def _build_saved_job(file_name: str, file_bytes: bytes, profile: JobProfile, title_fallback: str, suffix: str = '') -> JobDescription:
    resolved_title = profile.title or title_fallback or 'Uploaded Job Description'
    job = JobDescription(title=resolved_title)
    stem, ext = os.path.splitext(file_name)
    stored_name = f'jobs/{stem}{suffix}{ext}' if ext else f'jobs/{stem}{suffix}'
    job.description_file.save(stored_name, ContentFile(file_bytes), save=False)
    update_job_from_profile(job, profile)
    job.save()
    return job


def replace_active_job_description(file_obj) -> JobDescription | list[JobDescription]:
    profile_result = parse_job_upload(file_obj)  # Parse file → JobProfile(s)
    filename = os.path.basename(getattr(file_obj, 'name', '') or 'uploaded-job-description.txt')
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)
    file_bytes = File(file_obj).read()
    title_fallback = os.path.splitext(filename)[0].replace('_', ' ').replace('-', ' ').strip()

    if isinstance(profile_result, list):
        jobs = []
        for index, profile in enumerate(profile_result, 1):
            jobs.append(_build_saved_job(filename, file_bytes, profile, title_fallback, suffix=f'-{index}'))
        return jobs

    return _build_saved_job(filename, file_bytes, profile_result, title_fallback)


def remove_active_job_description(job_id=None) -> bool:
    job = get_job_description_by_id(job_id) if job_id is not None else get_active_job_description()
    if job is None:
        return False
    _delete_job_description(job)
    return True


def get_active_job_descriptions() -> list[JobDescription]:
    return list(JobDescription.objects.order_by('-created_at', '-id'))


# -----------------------------------------------------------------------
# APPLICANT <-> RESUME PROFILE SYNC
# These helpers copy data between the ORM model and the ResumeProfile dataclass.
# If you add a new parsed field (e.g. parsed_certifications), update both
# update_applicant_from_resume_profile() and build_resume_profile_from_applicant().
# -----------------------------------------------------------------------
def update_applicant_from_resume_profile(applicant, profile: ResumeProfile) -> None:
    applicant.resume_text = profile.raw_text
    applicant.parsed_skills = profile.skills
    applicant.parsed_education = profile.education
    applicant.parsed_experience = profile.experience
    applicant.parsed_locations = profile.locations


def update_job_from_profile(job, profile: JobProfile) -> None:
    job.title = profile.title or job.title
    job.reporting_to = profile.reporting_to or ''
    job.company = profile.company or ''
    job.location = profile.location or ''
    job.employment_type = profile.employment_type or ''
    job.experience_level = profile.experience_level or ''
    job.salary_range = profile.salary_range or ''
    job.remote_option = profile.remote_option
    job.required_skills = profile.required_skills
    job.preferred_skills = profile.preferred_skills
    job.responsibilities = profile.responsibilities
    job.qualifications = profile.qualifications
    job.education_requirements = profile.education_requirements
    job.certifications = profile.certifications
    job.benefits = profile.benefits
    job.industries = profile.industries
    job.raw_text = profile.raw_text


def build_resume_profile_from_applicant(applicant) -> ResumeProfile:
    return ResumeProfile(
        full_name=f'{applicant.first_name} {applicant.last_name}'.strip(),
        email=applicant.email,
        phone=applicant.contact_number,
        skills=list(applicant.parsed_skills or []),
        education=list(applicant.parsed_education or []),
        experience=list(applicant.parsed_experience or []),
        locations=list(applicant.parsed_locations or []),
        raw_text=applicant.resume_text or '',
    )


# -----------------------------------------------------------------------
# MATCH SCORING
# refresh_applicant_match() is the main entry point called from views.py
# (via refresh_applicant_matches). It:
#   1. Lazily parses the resume if not yet parsed.
#   2. Builds a ResumeProfile from stored fields.
#   3. Calls matcher.match_resume_to_job().
#   4. Saves the result (match_score, match_notes) back to the Applicant.
# DEBUG: if scores look wrong, set a breakpoint here or in matcher.py.
# -----------------------------------------------------------------------
def refresh_applicant_match(applicant, active_job: JobDescription | None):
    target_job = applicant.applied_job
    if target_job is None and applicant.job_preference_locked:
        # Applicant was locked to a job that has since been deleted; don't re-assign them
        return applicant, None
    if target_job is None:
        target_job = active_job  # No preferred job set yet; score against the current active job
    if target_job is None:
        return applicant, None  # No job at all; nothing to score against

    # Lazy resume parsing: only parse the file if structured fields are not already stored
    # (avoids re-parsing on every dashboard refresh)
    has_parsed_resume = bool(
        applicant.resume_text
        or applicant.parsed_skills
        or applicant.parsed_education
        or applicant.parsed_experience
        or applicant.parsed_locations
    )

    if not has_parsed_resume and applicant.resume:
        with applicant.resume.open('rb') as resume_file:
            profile = parse_resume_upload(resume_file)
        update_applicant_from_resume_profile(applicant, profile)
        applicant.save(update_fields=[
            'resume_text',
            'parsed_skills',
            'parsed_education',
            'parsed_experience',
            'parsed_locations',
        ])

    resume_profile = build_resume_profile_from_applicant(applicant)  # Build in-memory profile from stored fields
    match_result = get_matcher().match_resume_to_job(
        resume_profile,
        build_job_profile_from_model(target_job),  # Convert JobDescription ORM object → JobProfile dataclass
    )

    applicant.applied_job = target_job
    applicant.position = target_job.title
    applicant.match_score = round(match_result.overall_score * 100)  # Store as 0–100 integer
    applicant.match_notes = serialize_match_result(match_result)     # Store full breakdown as JSON
    applicant.save(update_fields=[
        'applied_job',
        'position',
        'match_score',
        'match_notes',
    ])
    return applicant, match_result


def refresh_applicant_matches(applicants, active_job: JobDescription | None):
    refreshed = []
    for applicant in applicants:
        refreshed_applicant, _ = refresh_applicant_match(applicant, active_job)
        refreshed.append(refreshed_applicant)
    return refreshed


def build_job_profile_from_model(job) -> JobProfile:
    return JobProfile(
        title=job.title,
        reporting_to=getattr(job, 'reporting_to', '') or None,
        company=job.company,
        location=job.location,
        employment_type=job.employment_type,
        experience_level=job.experience_level,
        salary_range=job.salary_range,
        remote_option=job.remote_option,
        required_skills=list(job.required_skills or []),
        preferred_skills=list(job.preferred_skills or []),
        responsibilities=list(job.responsibilities or []),
        qualifications=list(job.qualifications or []),
        education_requirements=list(job.education_requirements or []),
        certifications=list(job.certifications or []),
        benefits=list(job.benefits or []),
        industries=list(job.industries or []),
        raw_text=job.raw_text or '',
    )


def evaluate_best_job_match(resume_profile: ResumeProfile, jobs, selected_job=None):
    matcher = get_matcher()
    if selected_job is not None:
        # Shortcut: applicant selected a specific job; only score against that one
        result = matcher.match_resume_to_job(resume_profile, build_job_profile_from_model(selected_job))
        return selected_job, result

    # No preferred job selected; score against all jobs and return the best match
    best_job = None
    best_result = None
    for job in jobs:
        result = matcher.match_resume_to_job(resume_profile, build_job_profile_from_model(job))
        if best_result is None or result.overall_score > best_result.overall_score:
            best_job = job
            best_result = result
    return best_job, best_result


def serialize_match_result(result) -> dict:
    if result is None:
        return {}
    return {
        'overall_score': round(result.overall_score * 100),  # Convert 0.0-1.0 float → 0-100 integer for display
        'matching_skills': result.matching_skills[:8],        # Limit to 8 for dashboard display; increase if needed
        'missing_skills': result.missing_skills[:8],          # Limit to 8 for dashboard display; increase if needed
        'breakdown': {
            key: round(value * 100)                           # Each component score as 0-100 integer
            for key, value in result.breakdown.items()
        },
        'location_match': result.location_match,
        'remote_match': result.remote_match,
    }