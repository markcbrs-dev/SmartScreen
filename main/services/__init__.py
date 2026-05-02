"""
main/services/__init__.py — Public API for the services layer.

This file re-exports the most commonly used functions and classes so that
views.py and tests can import cleanly from `main.services` without needing
to know which sub-module each symbol lives in.

HOW TO MODIFY
-------------
  Add a new public function  → define it in the appropriate service module
                                (recruitment.py, entities.py, etc.) then add it
                                to the import and __all__ list here.
  Remove a function          → remove from imports, __all__, and usages in views.py.
"""

from .entities import JobMatchResult, JobProfile, ResumeProfile
from .recruitment import (
    extract_about_job_onward_text,
    build_resume_profile_from_applicant,
    extract_description_preview_text,
    extract_job_display_text,
    evaluate_best_job_match,
    extract_job_description_section,
    get_active_job_description,
    get_active_job_descriptions,
    get_job_description_by_id,
    parse_job_upload,
    parse_resume_upload,
    refresh_applicant_match,
    refresh_applicant_matches,
    remove_active_job_description,
    replace_active_job_description,
    serialize_match_result,
    update_applicant_from_resume_profile,
    update_job_from_profile,
)

__all__ = [
    'JobMatchResult',
    'JobProfile',
    'ResumeProfile',
    'extract_about_job_onward_text',
    'build_resume_profile_from_applicant',
    'extract_description_preview_text',
    'extract_job_display_text',
    'evaluate_best_job_match',
    'extract_job_description_section',
    'get_active_job_description',
    'get_active_job_descriptions',
    'get_job_description_by_id',
    'parse_job_upload',
    'parse_resume_upload',
    'refresh_applicant_match',
    'refresh_applicant_matches',
    'remove_active_job_description',
    'replace_active_job_description',
    'serialize_match_result',
    'update_applicant_from_resume_profile',
    'update_job_from_profile',
]