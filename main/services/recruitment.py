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
import re
from functools import lru_cache

from django.core.files.base import ContentFile, File

from main.models import JobDescription

from .entities import JobProfile, ResumeProfile
from .job_parser import JobDescriptionParser
from .matcher import ResumeJobMatcher
from .resume_parser import ResumeParser


INTERVIEW_MATCH_SCORE_THRESHOLD = 70


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


def _job_has_stale_education_domain_skills(job: JobDescription) -> bool:
    stale_skills = {'Information Technology', 'Computer Science'}
    combined_skills = set(job.required_skills or []) | set(job.preferred_skills or [])
    if not combined_skills.intersection(stale_skills):
        return False

    raw_text = (job.raw_text or '').lower()
    if not raw_text:
        return False

    return any(marker in raw_text for marker in (
        'bsit', 'bscs', 'it/cs', 'cs/it', 'information technology', 'computer science',
        'bachelor', 'master', 'degree', 'graduate',
    ))


def _refresh_stale_job_profile(job: JobDescription) -> JobDescription:
    if not _job_has_stale_education_domain_skills(job):
        return job

    profile = get_job_parser().parse_text(job.raw_text or '', filename=f'{job.title}.txt')
    if isinstance(profile, list):
        return job

    job.experience_level = profile.experience_level or ''
    job.salary_range = profile.salary_range or ''
    job.required_skills = profile.required_skills
    job.preferred_skills = profile.preferred_skills
    job.responsibilities = profile.responsibilities
    job.qualifications = profile.qualifications
    job.education_requirements = profile.education_requirements
    job.certifications = profile.certifications
    job.benefits = profile.benefits
    job.industries = profile.industries
    job.save(update_fields=[
        'experience_level',
        'salary_range',
        'required_skills',
        'preferred_skills',
        'responsibilities',
        'qualifications',
        'education_requirements',
        'certifications',
        'benefits',
        'industries',
    ])
    return job


# -----------------------------------------------------------------------
# JOB DESCRIPTION CRUD
# replace_active_job_description() is called by the dashboard view on upload.
# remove_active_job_description() is called by the dashboard view on remove.
# -----------------------------------------------------------------------
def get_active_job_description() -> JobDescription | None:
    job = JobDescription.objects.order_by('-created_at', '-id').first()
    return _refresh_stale_job_profile(job) if job is not None else None


def get_job_description_by_id(job_id) -> JobDescription | None:
    if not job_id:
        return None
    job = JobDescription.objects.filter(id=job_id).first()
    return _refresh_stale_job_profile(job) if job is not None else None


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
    jobs = list(JobDescription.objects.order_by('-created_at', '-id'))
    return [_refresh_stale_job_profile(job) for job in jobs]


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
        full_name=' '.join(part for part in [applicant.first_name, applicant.middle_name, applicant.last_name] if part).strip(),
        email=applicant.email,
        phone=applicant.contact_number,
        skills=list(applicant.parsed_skills or []),
        education=list(applicant.parsed_education or []),
        experience=list(applicant.parsed_experience or []),
        locations=list(applicant.parsed_locations or []),
        raw_text=applicant.resume_text or '',
    )


def _extract_alpha_tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r'[A-Za-z]+', value or '')
        if len(token) > 1
    }


def _count_email_name_token_matches(name: str, email: str) -> int:
    if not name or not email:
        return 0

    local_part = email.split('@', 1)[0]
    email_tokens = _extract_alpha_tokens(local_part)
    name_tokens = _extract_alpha_tokens(name)
    if not email_tokens or not name_tokens:
        return 0

    exact_matches = len(email_tokens & name_tokens)
    substring_matches = 0
    for name_token in name_tokens:
        if any(name_token in email_token or email_token in name_token for email_token in email_tokens):
            substring_matches += 1
    return max(exact_matches, substring_matches)


def _repair_applicant_name(applicant) -> None:
    parser = get_resume_parser()
    current_name = parser._normalize_full_name(' '.join(part for part in [applicant.first_name, applicant.middle_name, applicant.last_name] if part).strip())
    resume_text_name = parser._guess_name(applicant.resume_text or '') if applicant.resume_text else None

    resume_name = getattr(getattr(applicant, 'resume', None), 'name', None)
    filename_name = parser._extract_name_from_filename(os.path.basename(resume_name)) if resume_name else None
    derived_name = parser._select_best_full_name(resume_text_name, filename_name)
    if not derived_name:
        return

    if current_name:
        preferred_name = parser._select_best_full_name(current_name, derived_name)
        if preferred_name == current_name and parser._names_share_tokens(current_name, derived_name):
            return

        current_email_matches = _count_email_name_token_matches(current_name, applicant.email or '')
        derived_email_matches = _count_email_name_token_matches(derived_name, applicant.email or '')
        if derived_email_matches < current_email_matches:
            return
        if (
            derived_email_matches == current_email_matches
            and parser._names_share_tokens(current_name, derived_name)
            and preferred_name == current_name
        ):
            return
        if (
            derived_email_matches == current_email_matches
            and not parser._names_share_tokens(current_name, derived_name)
            and parser._count_name_parts(derived_name) <= parser._count_name_parts(current_name)
        ):
            return

    first_name, middle_name, last_name = parser.split_name_components(derived_name)
    first_name = first_name[:100]
    middle_name = middle_name[:100]
    last_name = last_name[:100]
    if applicant.first_name == first_name and applicant.middle_name == middle_name and applicant.last_name == last_name:
        return

    applicant.first_name = first_name
    applicant.middle_name = middle_name
    applicant.last_name = last_name
    applicant.save(update_fields=['first_name', 'middle_name', 'last_name'])


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
    def has_current_breakdown(match_notes, target_job_id, available_job_ids) -> bool:
        if not isinstance(match_notes, dict) or not match_notes:
            return False

        breakdown = match_notes.get('breakdown')
        if not isinstance(breakdown, dict) or not breakdown:
            return False

        allowed_keys = {'skill_score', 'experience_score', 'education_score'}
        if any(key not in allowed_keys for key in breakdown):
            return False

        overall_score = match_notes.get('overall_score')
        if not isinstance(overall_score, int):
            return False

        contribution_total = sum(
            value for value in breakdown.values()
            if isinstance(value, int)
        )
        if contribution_total != overall_score:
            return False

        if match_notes.get('target_job_id') != target_job_id:
            return False

        saved_job_ids = match_notes.get('scored_job_ids')
        if not isinstance(saved_job_ids, list):
            return False

        normalized_saved_job_ids = []
        for job_id in saved_job_ids:
            if not isinstance(job_id, int):
                return False
            normalized_saved_job_ids.append(job_id)

        return normalized_saved_job_ids == available_job_ids

    _repair_applicant_name(applicant)

    available_jobs = list(active_job or []) if isinstance(active_job, (list, tuple)) else ([active_job] if active_job is not None else [])
    available_job_ids = [job.id for job in available_jobs]
    current_active_job = available_jobs[0] if available_jobs else None

    target_job = applicant.applied_job
    if target_job is None and applicant.job_preference_locked:
        # Applicant was locked to a job that has since been deleted; don't re-assign them
        return applicant, None
    if target_job is None:
        target_job = current_active_job  # No preferred job set yet; score against the current active job
    if target_job is None:
        return applicant, None  # No job at all; nothing to score against

    if not available_jobs:
        available_jobs = [target_job]
        available_job_ids = [target_job.id]

    # Skip re-scoring if the applicant already has a valid score for this exact job.
    # match_notes is {} by default and non-empty after the first score, so it is a
    # reliable signal that scoring has already run.  The score only becomes stale
    # when the applicant's job FK changes (e.g. old job deleted → SET_NULL) or when
    # a new job is uploaded and this applicant has no locked preference — in both
    # cases applied_job_id will differ from target_job.id, so the guard is bypassed.
    if applicant.applied_job_id == target_job.id and has_current_breakdown(applicant.match_notes, target_job.id, available_job_ids):
        return applicant, None

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
    scored_jobs = score_resume_against_jobs(resume_profile, available_jobs)
    target_match_result = next(
        (result for job, result in scored_jobs if job.id == target_job.id),
        None,
    )
    if target_match_result is None:
        target_match_result = get_matcher().match_resume_to_job(
            resume_profile,
            build_job_profile_from_model(target_job),  # Convert JobDescription ORM object → JobProfile dataclass
        )
        scored_jobs = [(target_job, target_match_result)]

    recommendation_job, recommendation_result = _select_best_job_score(
        scored_jobs,
        exclude_job_id=target_job.id,
    )

    applicant.applied_job = target_job
    applicant.position = target_job.title
    applicant.match_score = round(target_match_result.overall_score * 100)  # Store as 0–100 integer
    applicant.status = 'INTERVIEW' if applicant.match_score >= INTERVIEW_MATCH_SCORE_THRESHOLD else 'NEW'
    applicant.match_notes = serialize_match_result(
        target_match_result,
        matched_job=target_job,
        recommendation_job=recommendation_job,
        recommendation_result=recommendation_result,
        scored_jobs=scored_jobs,
    )     # Store full breakdown as JSON
    applicant.save(update_fields=[
        'applied_job',
        'position',
        'status',
        'match_score',
        'match_notes',
    ])
    return applicant, target_match_result


def refresh_applicant_matches(applicants, active_job: JobDescription | None):
    refreshed = []
    for applicant in applicants:
        refreshed_applicant, _ = refresh_applicant_match(applicant, active_job)
        refreshed.append(refreshed_applicant)
    return refreshed


def build_job_profile_from_model(job) -> JobProfile:
    required_skills = list(job.required_skills or [])
    preferred_skills = list(job.preferred_skills or [])
    if job.raw_text:
        parsed_required_skills, parsed_preferred_skills = get_job_parser().extract_skills(job.raw_text)
        if parsed_required_skills or parsed_preferred_skills:
            required_skills = parsed_required_skills
            preferred_skills = parsed_preferred_skills

    return JobProfile(
        title=job.title,
        reporting_to=getattr(job, 'reporting_to', '') or None,
        company=job.company,
        location=job.location,
        employment_type=job.employment_type,
        experience_level=job.experience_level,
        salary_range=job.salary_range,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        responsibilities=list(job.responsibilities or []),
        qualifications=list(job.qualifications or []),
        education_requirements=list(job.education_requirements or []),
        certifications=list(job.certifications or []),
        benefits=list(job.benefits or []),
        industries=list(job.industries or []),
        raw_text=job.raw_text or '',
    )


def _serialize_job_recommendation(job, result) -> dict:
    if job is None or result is None:
        return {}
    return {
        'job_id': job.id,
        'job_title': job.title,
        'overall_score': round(result.overall_score * 100),
    }


def score_resume_against_jobs(resume_profile: ResumeProfile, jobs) -> list[tuple[JobDescription, object]]:
    matcher = get_matcher()
    return [
        (job, matcher.match_resume_to_job(resume_profile, build_job_profile_from_model(job)))
        for job in jobs
    ]


def _select_best_job_score(scored_jobs, *, exclude_job_id=None):
    best_job = None
    best_result = None
    for job, result in scored_jobs:
        if exclude_job_id is not None and job.id == exclude_job_id:
            continue
        if best_result is None or result.overall_score > best_result.overall_score:
            best_job = job
            best_result = result
    return best_job, best_result


def evaluate_best_job_match(resume_profile: ResumeProfile, jobs, selected_job=None):
    if selected_job is not None:
        # Shortcut: applicant selected a specific job; only score against that one
        result = get_matcher().match_resume_to_job(resume_profile, build_job_profile_from_model(selected_job))
        return selected_job, result

    # No preferred job selected; score against all jobs and return the best match
    return _select_best_job_score(score_resume_against_jobs(resume_profile, jobs))


def serialize_match_result(result, *, matched_job=None, recommendation_job=None, recommendation_result=None, scored_jobs=None) -> dict:
    if result is None:
        return {}
    payload = {
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
    if matched_job is not None:
        payload['matched_job'] = {
            'job_id': matched_job.id,
            'job_title': matched_job.title,
        }
        payload['target_job_id'] = matched_job.id
    recommendation = _serialize_job_recommendation(recommendation_job, recommendation_result)
    if recommendation:
        payload['recommendation'] = recommendation
    if scored_jobs is not None:
        payload['scored_job_ids'] = [job.id for job, _ in scored_jobs]
        payload['scored_job_count'] = len(scored_jobs)
    return payload