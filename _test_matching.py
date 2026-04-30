import sys, os
sys.path.insert(0, r'F:\Documents (F)\Dev\THESIS\smartscreen_django\SmartScreen')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smscrweb.settings')

import django
django.setup()

from main.services.entities import ResumeProfile, JobProfile
from main.services.matcher import ResumeJobMatcher

matcher = ResumeJobMatcher()

# ---------- Shared JD: Python Backend Engineer ----------
job = JobProfile(
    title='Python Backend Engineer',
    required_skills=['Python', 'Django', 'REST API', 'PostgreSQL', 'Docker'],
    preferred_skills=['Redis', 'AWS', 'Kubernetes'],
    experience_level='senior',
    education_requirements=['Bachelor degree in Computer Science or related field'],
    raw_text=(
        'We need a Python backend engineer with Django REST API PostgreSQL Docker '
        'Redis AWS Kubernetes. Senior level. Bachelor degree.'
    ),
)

scenarios = [
    (
        'FULLY QUALIFIED',
        ResumeProfile(
            skills=['Python', 'Django', 'REST API', 'PostgreSQL', 'Docker', 'Redis', 'AWS'],
            experience=['Senior Python Engineer, 6 years experience'],
            education=['Bachelor of Science in Computer Science'],
            raw_text='Senior backend engineer skilled in Python Django REST API PostgreSQL Docker Redis AWS. 6 years experience.',
        ),
    ),
    (
        'PARTIALLY QUALIFIED (3/5 required)',
        ResumeProfile(
            skills=['Python', 'Django', 'REST API'],
            experience=['Mid level developer, 3 years'],
            education=['Bachelor of Science'],
            raw_text='Python Django REST API developer 3 years.',
        ),
    ),
    (
        'WRONG TECH STACK (JS developer)',
        ResumeProfile(
            skills=['JavaScript', 'React', 'Node.js', 'MongoDB'],
            experience=['Senior frontend developer, 5 years'],
            education=['Bachelor degree in Computer Science'],
            raw_text='Senior JavaScript React Node.js MongoDB developer 5 years.',
        ),
    ),
    (
        'UNQUALIFIED (no relevant skills)',
        ResumeProfile(
            skills=['Excel', 'Word', 'PowerPoint'],
            experience=['Office administrator, 2 years'],
            education=['High school diploma'],
            raw_text='Office administrator with Excel Word PowerPoint 2 years experience.',
        ),
    ),
    (
        'SOFT SKILLS ONLY',
        ResumeProfile(
            skills=['Communication', 'Teamwork', 'Leadership', 'Problem Solving'],
            experience=['Team lead, 3 years'],
            education=['Bachelor of Arts in Management'],
            raw_text='Team lead with strong communication teamwork leadership problem solving skills.',
        ),
    ),
    (
        'OVERQUALIFIED (all required + preferred)',
        ResumeProfile(
            skills=['Python', 'Django', 'REST API', 'PostgreSQL', 'Docker', 'Redis', 'AWS', 'Kubernetes', 'Terraform'],
            experience=['Principal Engineer, 12 years experience, lead, director'],
            education=['Master degree in Computer Science'],
            raw_text='Principal engineer Python Django REST API PostgreSQL Docker Redis AWS Kubernetes Terraform 12 years.',
        ),
    ),
]

W = 44
print('=' * 78)
print(f"{'SCENARIO':<{W}} {'OVERALL':>7} {'SKILL':>7} {'EXP':>6} {'EDU':>6} {'KW':>6}")
print('=' * 78)
for label, resume in scenarios:
    r = matcher.match_resume_to_job(resume, job)
    print(f"{label:<{W}} {r.overall_score:>6.1%} {r.skill_match_score:>6.1%} {r.experience_match_score:>5.1%} {r.education_match_score:>5.1%} {r.keyword_match_score:>5.1%}")
    if r.matching_skills:
        print(f"  matched : {r.matching_skills}")
    if r.missing_skills:
        print(f"  missing : {r.missing_skills}")
    print()
print('=' * 78)
