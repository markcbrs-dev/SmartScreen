import os
from functools import lru_cache

from django.core.files.base import File

from main.models import JobDescription

from .entities import JobProfile, ResumeProfile
from .job_parser import JobDescriptionParser
from .matcher import ResumeJobMatcher
from .resume_parser import ResumeParser


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


def extract_job_description_section(raw_text: str) -> str:
    return get_job_parser().extract_job_description_section(raw_text or '')


def extract_description_preview_text(raw_text: str) -> str:
    return get_job_parser().extract_description_preview_text(raw_text or '')


def extract_about_job_onward_text(raw_text: str) -> str:
    return get_job_parser().extract_about_job_onward_text(raw_text or '')


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


def replace_active_job_description(file_obj) -> JobDescription:
    profile = parse_job_upload(file_obj)
    filename = os.path.basename(getattr(file_obj, 'name', '') or 'uploaded-job-description.txt')
    resolved_title = profile.title or os.path.splitext(filename)[0].replace('_', ' ').replace('-', ' ').strip() or 'Uploaded Job Description'

    job = JobDescription(title=resolved_title)
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)
    job.description_file.save(f'jobs/{filename}', File(file_obj), save=False)
    update_job_from_profile(job, profile)
    job.save()
    return job


def remove_active_job_description(job_id=None) -> bool:
    job = get_job_description_by_id(job_id) if job_id is not None else get_active_job_description()
    if job is None:
        return False
    _delete_job_description(job)
    return True


def get_active_job_descriptions() -> list[JobDescription]:
    return list(JobDescription.objects.order_by('-created_at', '-id'))


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
    target_job = applicant.applied_job
    if target_job is None and applicant.job_preference_locked:
        return applicant, None
    if target_job is None:
        target_job = active_job
    if target_job is None:
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
        build_job_profile_from_model(target_job),
    )

    applicant.applied_job = target_job
    applicant.position = target_job.title
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