from .entities import JobProfile, ResumeProfile
from .job_parser import JobDescriptionParser
from .matcher import ResumeJobMatcher
from .resume_parser import ResumeParser

resume_parser = ResumeParser()
job_parser = JobDescriptionParser()
matcher = ResumeJobMatcher()


def parse_resume_upload(file_obj) -> ResumeProfile:
    return resume_parser.parse(file_obj, filename=getattr(file_obj, 'name', None))


def parse_job_upload(file_obj) -> JobProfile:
    return job_parser.parse(file_obj, filename=getattr(file_obj, 'name', None))


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