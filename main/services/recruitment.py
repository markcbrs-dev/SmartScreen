from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files.base import File

from main.models import JobDescription

from .entities import JobProfile, ResumeProfile
from .job_parser import JobDescriptionParser
from .matcher import ResumeJobMatcher
from .resume_parser import ResumeParser

SAMPLE_JOB_STORAGE_NAME = 'jobs/job-description-sample.txt'


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


def parse_job_upload(file_obj) -> JobProfile:
    return get_job_parser().parse(file_obj, filename=getattr(file_obj, 'name', None))


def sync_sample_job_description() -> JobDescription | None:
    sample_path = Path(getattr(settings, 'SAMPLE_JOB_DESCRIPTION_FILE', ''))
    if not sample_path.exists():
        return None

    with sample_path.open('rb') as sample_file:
        profile = parse_job_upload(sample_file)

    resolved_title = profile.title or sample_path.stem.replace('_', ' ').replace('-', ' ').strip()
    job = JobDescription.objects.filter(description_file=SAMPLE_JOB_STORAGE_NAME).first()
    if job is None:
        job = JobDescription.objects.filter(title=resolved_title).first()
    if job is None:
        job = JobDescription(title=resolved_title)

    job.title = resolved_title
    with sample_path.open('rb') as sample_file:
        job.description_file.save(SAMPLE_JOB_STORAGE_NAME, File(sample_file), save=False)
    update_job_from_profile(job, profile)
    job.save()
    return job


def get_active_job_descriptions() -> list[JobDescription]:
    active_job = sync_sample_job_description()
    return [active_job] if active_job is not None else []


def update_applicant_from_resume_profile(applicant, profile: ResumeProfile) -> None:
    applicant.resume_text = profile.raw_text
    applicant.parsed_skills = profile.skills
    applicant.parsed_education = profile.education
    applicant.parsed_experience = profile.experience
    applicant.parsed_locations = profile.locations


def update_job_from_profile(job, profile: JobProfile) -> None:
    job.title = profile.title or job.title
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


def refresh_applicant_match(applicant, active_job: JobDescription | None):
    if active_job is None:
        return applicant, None

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

    resume_profile = build_resume_profile_from_applicant(applicant)
    match_result = get_matcher().match_resume_to_job(
        resume_profile,
        build_job_profile_from_model(active_job),
    )

    applicant.applied_job = active_job
    applicant.position = active_job.title
    applicant.match_score = round(match_result.overall_score * 100)
    applicant.match_notes = serialize_match_result(match_result)
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
        result = matcher.match_resume_to_job(resume_profile, build_job_profile_from_model(selected_job))
        return selected_job, result

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
        'overall_score': round(result.overall_score * 100),
        'matching_skills': result.matching_skills[:8],
        'missing_skills': result.missing_skills[:8],
        'breakdown': {
            key: round(value * 100)
            for key, value in result.breakdown.items()
        },
        'location_match': result.location_match,
        'remote_match': result.remote_match,
    }