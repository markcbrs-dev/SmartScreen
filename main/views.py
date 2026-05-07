"""
main/views.py — HTTP request handlers (views) for all app pages.

OVERVIEW
--------
All user-facing and HR-facing page logic lives here.
Views are wired to URLs in main/urls.py.

HOW TO MODIFY
-------------
  Add a new page      → create a new view function here, add a URL pattern in urls.py,
                        create a template in main/templates/main/.
  Change form logic   → find the relevant view (apply, hr_register, login_view) and edit
                        the POST branch.
  Change dashboard    → edit the `dashboard` view; job upload/remove logic is in the
                        `if action == ...` blocks.
  Change text display → edit the private helpers (_build_formatted_text_sections,
                        _build_dashboard_preview_sections, _truncate_formatted_text, etc.)
                        These only affect how job description text is rendered in templates.
  Add a new action    → add another `if action == '...'` branch inside dashboard's POST handler.
  Change audit log    → edit `audit_log_view`; it queries Applicant.history (simple-history).
  Change session      → login_view controls session expiry ('remember me' sets 2 weeks).

VIEWS IN THIS FILE
------------------
  login_view      — HR login form (GET/POST)
  hr_register     — HR self-registration; account starts inactive until admin approves
  logout_view     — POST-only logout
  dashboard       — HR dashboard: job upload/removal, applicant list with match scores
  audit_log_view  — shows last 50 audit entries from Applicant change history
  apply           — public applicant submission form (GET/POST)

PRIVATE HELPERS (text rendering — not views)
--------------------------------------------
  _normalize_description_line, _render_inline_formatting, _visible_text_length,
  _truncate_formatted_text, _append_section_block, _is_description_heading,
  _build_formatted_text_sections, _build_description_preview_sections,
  _build_dashboard_preview_sections, _get_job_display_text
"""

from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.urls import reverse
from html import escape
from collections import defaultdict
import re
from .models import Applicant, JobDescription
import os
from django.contrib.auth.decorators import login_required, user_passes_test
from .services.job_parser import JobDescriptionParser
from .services.skills import normalize_employment_type

_staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url='login')
_job_description_parser = JobDescriptionParser()

from .services import (
    extract_about_job_onward_text,
    build_resume_profile_from_applicant,
    extract_description_preview_text,
    extract_job_display_text,
    evaluate_best_job_match,
    extract_job_description_section,
    get_active_job_description,
    get_active_job_descriptions,
    get_job_description_by_id,
    parse_resume_upload,
    refresh_applicant_matches,
    remove_active_job_description,
    replace_active_job_description,
    serialize_match_result,
    update_applicant_from_resume_profile,
)


# -----------------------------------------------------------------------
# PRIVATE TEXT-RENDERING HELPERS
# These format job description text for HTML templates.
# Modify these if you need to change how job descriptions are displayed
# (e.g. bullet rendering, bold/markdown syntax, preview truncation).
# -----------------------------------------------------------------------
def _normalize_description_line(line: str) -> str:
    return re.sub(r'\*\*(.*?)\*\*', r'\1', line).strip().rstrip(':').lower()


def _render_inline_formatting(text: str) -> str:
    parts: list[str] = []
    last_index = 0

    for match in re.finditer(r'\*\*(.+?)\*\*', text):
        parts.append(escape(text[last_index:match.start()]))
        parts.append(f'<strong>{escape(match.group(1))}</strong>')
        last_index = match.end()

    parts.append(escape(text[last_index:]))
    return ''.join(parts)


def _visible_text_length(text: str) -> int:
    return len(re.sub(r'\*\*', '', text))


def _truncate_formatted_text(text: str, max_chars: int) -> str:
    if _visible_text_length(text) <= max_chars:
        return text

    remaining = max_chars
    parts: list[str] = []

    for match in re.finditer(r'\*\*.+?\*\*|[^*]+|\*', text):
        token = match.group(0)
        if token.startswith('**') and token.endswith('**') and len(token) >= 4:
            inner = token[2:-2]
            inner_length = len(inner)
            if inner_length <= remaining:
                parts.append(token)
                remaining -= inner_length
                continue

            if remaining > 0:
                parts.append(f'**{inner[:remaining].rstrip()}**')
            break

        token_length = len(token)
        if token_length <= remaining:
            parts.append(token)
            remaining -= token_length
            continue

        if remaining > 0:
            parts.append(token[:remaining].rstrip())
        break

    truncated = ''.join(parts).rstrip()
    return f'{truncated}...'


def _append_section_block(section: dict[str, object], block_type: str, html: str) -> None:
    if not html:
        return

    blocks = section['blocks']
    if block_type == 'list':
        if blocks and blocks[-1]['type'] == 'list':
            blocks[-1]['items_html'].append(html)
            return
        blocks.append({'type': 'list', 'items_html': [html]})
        return

    blocks.append({'type': 'paragraph', 'html': html})


def _is_description_heading(line: str) -> bool:
    normalized = _normalize_description_line(line)
    if normalized in {
        'about the job',
        'about this job',
        'job description',
        'description',
        'overview',
        'responsibilities',
        'duties',
        'qualifications',
        'requirements',
        'benefits',
        'education',
    }:
        return True
    return line.endswith(':') and len(line) <= 120


def _build_formatted_text_sections(text: str, default_heading: str | None = None) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    current_section: dict[str, object] | None = None

    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if _is_description_heading(line):
            current_section = {
                'heading_html': _render_inline_formatting(line if line.endswith(':') else f'{line}:'),
                'blocks': [],
            }
            sections.append(current_section)
            continue

        is_bullet = bool(re.match(r'^(?:[•\-]\s+|\*\s+)', line))
        cleaned = re.sub(r'^(?:[•\-]\s+|\*\s+)', '', line).strip()
        if not cleaned:
            continue

        if current_section is None:
            current_section = {
                'heading_html': _render_inline_formatting(default_heading) if default_heading else None,
                'blocks': [],
            }
            sections.append(current_section)

        _append_section_block(current_section, 'list' if is_bullet else 'paragraph', _render_inline_formatting(cleaned))

    return sections


def _build_description_preview_sections(text: str) -> list[dict[str, object]]:
    return _build_formatted_text_sections(text, default_heading='Job Description:')


def _build_dashboard_preview_sections(text: str, max_chars: int = 250) -> list[dict[str, object]]:
    return _build_description_preview_sections(_truncate_formatted_text(text, max_chars))


def _get_job_display_text(job: JobDescription) -> str:
    description_file = getattr(job, 'description_file', None)
    if not description_file:
        return job.raw_text or ''

    try:
        description_file.open('rb')
        return extract_job_display_text(description_file, filename=os.path.basename(description_file.name))
    except (OSError, ValueError):
        return job.raw_text or ''
    finally:
        try:
            description_file.close()
        except OSError:
            pass

# -----------------------------------------------------------------------
# AUTH VIEWS
# DEBUG: check messages.error(request, ...) calls for form validation issues.
# To change session duration, edit request.session.set_expiry() in login_view.
# To change approval behaviour, see is_active=False in hr_register.
# -----------------------------------------------------------------------
def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            remember = request.POST.get('remember') == 'on'
            if remember:
                request.session.set_expiry(1209600)  # 2 weeks
            else:
                request.session.set_expiry(0)  # expire on browser close
            messages.success(request, 'Logged in successfully.')
            return redirect('dashboard')
        else:
            email = request.POST.get('username', '').strip()
            password = request.POST.get('password', '')
            pending_user = User.objects.filter(username=email, is_active=False, is_staff=True).first()

            if pending_user and password and pending_user.check_password(password):
                messages.error(request, 'Wait for Admin approval.')
            else:
                messages.error(request, 'Wrong Email or Password')
    else:
        form = AuthenticationForm()
    return render(request, 'main/hr_login.html', {'form': form})

def hr_register(request):
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        if not first_name or not last_name or not email or not password:
            messages.error(request, 'Please fill in all required fields.')
            return render(request, 'main/hr_register.html')

        if User.objects.filter(username=email).exists():
            messages.error(request, 'A user with that email already exists.')
            return render(request, 'main/hr_register.html')

        user = User.objects.create_user(username=email, email=email, password=password,
                                        first_name=first_name, last_name=last_name)
        user.is_staff = True
        user.is_active = False # Keep user inactive until admin approval
        user.save()

        messages.success(request, 'Registration successful. Please wait for admin approval.')
        return redirect('login')

    return render(request, 'main/hr_register.html')

@_staff_required
def logout_view(request):
    if request.method != 'POST':
        return redirect('dashboard')
    auth_logout(request)
    messages.success(request, 'Logged out successfully.')
    return redirect('login')

# -----------------------------------------------------------------------
# DASHBOARD VIEW
# Handles POST actions via `action` field in the form:
#   'upload_job_description'    — validates + calls replace_active_job_description()
#   'remove_job_description'    — calls remove_active_job_description()
#   'update_applicant_status'   — sets applicant.status to the given value
# GET: loads all jobs and applicants, refreshes match scores, renders dashboard.
# DEBUG: check services/recruitment.py for replace_active_job_description and
#        refresh_applicant_matches if scores are wrong or uploads fail.
# -----------------------------------------------------------------------
@_staff_required
def dashboard(request):
    def redirect_to_dashboard(panel: str | None = None):
        url = reverse('dashboard')
        if panel:
            url = f'{url}?panel={panel}'
        return redirect(url)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'upload_job_description':
            uploaded_job_file = request.FILES.get('job_description_file')
            if not uploaded_job_file:
                messages.error(request, 'Please choose a job description file to upload.')
                return redirect_to_dashboard('jobs')

            allowed_ext = {'.txt', '.docx'}
            ext = os.path.splitext(uploaded_job_file.name)[1].lower()
            if ext not in allowed_ext:
                messages.error(request, 'Unsupported job description format. Use TXT or DOCX.')
                return redirect_to_dashboard('jobs')

            max_size = getattr(settings, 'MAX_JOB_DESCRIPTION_SIZE', 512 * 1024)
            if uploaded_job_file.size > max_size:
                messages.error(request, f'Job description file too large (max {max_size // 1024} KB).')
                return redirect_to_dashboard('jobs')

            try:
                job_result = replace_active_job_description(uploaded_job_file)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                if isinstance(job_result, list):
                    titles = ', '.join(job.title for job in job_result[:3])
                    if len(job_result) > 3:
                        titles = f'{titles}, ...'
                    messages.success(request, f'Uploaded {len(job_result)} job descriptions: {titles}.')
                else:
                    messages.success(request, f'Uploaded job description: {job_result.title}.')
            return redirect_to_dashboard('jobs')

        if action == 'remove_job_description':
            job_id = request.POST.get('job_id', '').strip()
            if remove_active_job_description(job_id or None):
                messages.success(request, 'Uploaded job description removed.')
            else:
                messages.info(request, 'No uploaded job description to remove.')
            return redirect_to_dashboard('jobs')

        if action == 'remove_applicant':
            applicant_id = request.POST.get('applicant_id', '').strip()
            redirect_panel = request.POST.get('redirect_panel', 'interview').strip()
            applicant = Applicant.objects.filter(id=applicant_id).first() if applicant_id else None
            if applicant is None:
                messages.error(request, 'Applicant not found.')
            else:
                applicant_name = f'{applicant.first_name} {applicant.last_name}'.strip()
                if applicant.resume:
                    applicant.resume.delete(save=False)
                applicant.delete()
                messages.success(request, f'{applicant_name} was removed.')
            return redirect_to_dashboard(redirect_panel)

        # Move an applicant to a new status (e.g. NEW → INTERVIEW).
        # The status value must be one of Applicant.STATUS_CHOICES.
        # Redirects back to the panel specified by 'redirect_panel' (defaults to 'dashboard').
        if action == 'update_applicant_status':
            applicant_id = request.POST.get('applicant_id', '').strip()
            new_status = request.POST.get('new_status', '').strip()
            redirect_panel = request.POST.get('redirect_panel', 'dashboard').strip()
            valid_statuses = {choice[0] for choice in Applicant.STATUS_CHOICES}
            applicant = Applicant.objects.filter(id=applicant_id).first() if applicant_id else None
            if applicant is None:
                messages.error(request, 'Applicant not found.')
            elif new_status not in valid_statuses:
                messages.error(request, 'Invalid status value.')
            else:
                applicant._history_user = request.user
                applicant.status = new_status
                applicant.save(update_fields=['status'])
                label = dict(Applicant.STATUS_CHOICES).get(new_status, new_status)
                messages.success(request, f'{applicant.first_name} {applicant.last_name} moved to "{label}".')
            return redirect_to_dashboard(redirect_panel)

    jobs = get_active_job_descriptions()
    for job in jobs:
        job.employment_type = normalize_employment_type(job.employment_type) or ''
        job.display_text = _get_job_display_text(job)
        if not getattr(job, 'reporting_to', ''):
            job.reporting_to = _job_description_parser.extract_reporting_to(job.display_text)
        job.description_text = extract_job_description_section(job.display_text)
        job.description_preview_text = extract_description_preview_text(job.display_text)
        job.description_preview_sections = _build_description_preview_sections(extract_about_job_onward_text(job.display_text))
        job.description_preview_excerpt_sections = _build_dashboard_preview_sections(extract_about_job_onward_text(job.display_text))
    active_job = jobs[0] if jobs else None
    applicants = list(Applicant.objects.select_related('applied_job').all().order_by('-created_at'))
    applicants = refresh_applicant_matches(applicants, active_job)

    grouped_applicants: dict[tuple[str, object], list[Applicant]] = defaultdict(list)
    for applicant in applicants:
        if applicant.applied_job_id:
            group_key = ('job', applicant.applied_job_id)
        elif applicant.position:
            group_key = ('position', applicant.position.strip().lower())
        else:
            group_key = ('unassigned', 'pending')
        grouped_applicants[group_key].append(applicant)

    applicant_position_groups: list[dict[str, object]] = []
    grouped_lookup = {
        ('job', job.id): {
            'group_title': job.title,
            'group_subtitle': f'{job.company} · {job.location}' if job.company and job.location else job.company or job.location or 'No applicants yet.',
            'group_job': job,
            'applicants': [],
        }
        for job in jobs
    }

    for group_key, group_items in grouped_applicants.items():
        sorted_group = sorted(group_items, key=lambda a: (-(a.match_score or 0), a.created_at))
        for rank_idx, applicant in enumerate(sorted_group, 1):
            applicant.rank = rank_idx

        existing_group = grouped_lookup.get(group_key)
        if existing_group is not None:
            existing_group['applicants'] = sorted_group
            continue

        sample = sorted_group[0]
        group_title = sample.position or 'Pending match'
        group_subtitle = 'Applicants without a linked uploaded job description.'
        if group_key[0] == 'unassigned':
            group_title = 'Pending Match'
            group_subtitle = 'Applicants without a stored job match yet.'

        grouped_lookup[group_key] = {
            'group_title': group_title,
            'group_subtitle': group_subtitle,
            'group_job': getattr(sample, 'applied_job', None),
            'applicants': sorted_group,
        }

    for job in jobs:
        job_group = grouped_lookup[('job', job.id)]
        if job_group['applicants']:
            applicant_position_groups.append(job_group)

    for group_key, group in grouped_lookup.items():
        if group_key[0] != 'job':
            applicant_position_groups.append(group)

    applicants = [
        applicant
        for group in applicant_position_groups
        for applicant in group['applicants']
    ]
    total = len(applicants)
    new_count = sum(1 for applicant in applicants if applicant.status == 'NEW')
    interview_count = sum(1 for applicant in applicants if applicant.status == 'INTERVIEW')
    # The Status panel now shows all applicants, with actions varying by status.
    interview_applicants = applicants
    interview_position_groups = applicant_position_groups
    context = {
        'applicants': applicants,
        'applicant_position_groups': applicant_position_groups,
        'interview_applicants': interview_applicants,
        'interview_position_groups': interview_position_groups,
        'jobs': jobs,
        'active_job': active_job,
        'total': total,
        'new_count': new_count,
        'interview_count': interview_count,
    }
    return render(request, 'main/dashboard.html', context)

# -----------------------------------------------------------------------
# AUDIT LOG VIEW
# Shows the 50 most recent changes to Applicant records tracked by
# django-simple-history. Template: main/audit_log.html.
# To show more records, change the [:50] slice below.
# -----------------------------------------------------------------------
@_staff_required
def audit_log_view(request):
    # This fetches the 50 most recent changes tracked by simple-history
    logs = Applicant.history.all().order_by('-history_date')[:50]
    return render(request, 'main/audit_log.html', {'logs': logs})

# -----------------------------------------------------------------------
# APPLY VIEW (public applicant form)
# GET:  renders the job list and description preview for applicants.
# POST: validates fields + resume file, parses resume, creates Applicant,
#       evaluates best job match, then saves the match result.
# DEBUG: check services/resume_parser.py if parsing fails.
#        Check services/matcher.py if match scores look wrong.
#        Check services/recruitment.py evaluate_best_job_match for scoring logic.
# To change allowed file types or max size, edit allowed_ext / max_size below.
# -----------------------------------------------------------------------
def apply(request):
    available_jobs = get_active_job_descriptions()
    for job in available_jobs:
        job.employment_type = normalize_employment_type(job.employment_type) or ''
    active_job = available_jobs[0] if available_jobs else None
    if active_job is not None:
        active_job.display_text = _get_job_display_text(active_job)
        active_job.description_preview_text = extract_about_job_onward_text(active_job.display_text)
        active_job.description_preview_sections = _build_description_preview_sections(active_job.description_preview_text)
    context = {
        'available_jobs': available_jobs,
        'active_job': active_job,
        'job_details': [
            {
                'id': job.id,
                'title': job.title,
                'company': job.company,
                'location': job.location,
                'employment_type': job.employment_type,
                'remote_option': job.remote_option,
                'responsibilities': list(job.responsibilities or []),
                'description_preview_text': extract_about_job_onward_text(_get_job_display_text(job)),
                'description_preview_sections': _build_description_preview_sections(extract_about_job_onward_text(_get_job_display_text(job))),
            }
            for job in available_jobs
        ],
    }
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        contact = request.POST.get('contact', request.POST.get('contact_number', '')).strip()
        email = request.POST.get('email', '').strip()
        resume_file = request.FILES.get('resume')
        selected_job_id = request.POST.get('job_id', '').strip()

        if not first_name or not last_name or not contact or not email:
            messages.error(request, 'Please fill in all required fields.')
            return render(request, 'main/applicant.html', context)

        if not re.fullmatch(r'\d{11}', contact):
            messages.error(request, 'Contact number must be exactly 11 digits.')
            return render(request, 'main/applicant.html', context)

        if not resume_file:
            messages.error(request, 'Please upload a resume file.')
            return render(request, 'main/applicant.html', context)

        allowed_ext = {'.pdf', '.docx'}
        ext = os.path.splitext(resume_file.name)[1].lower()
        if ext not in allowed_ext:
            messages.error(request, 'Unsupported file type. Use PDF or DOCX.')
            return render(request, 'main/applicant.html', context)

        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
        if resume_file.size > max_size:
            messages.error(request, f'Resume file too large (max {max_size // 1024 // 1024} MB).')
            return render(request, 'main/applicant.html', context)

        selected_job = get_job_description_by_id(selected_job_id)

        if not available_jobs:
            messages.error(request, 'No uploaded job description is available right now.')
            return render(request, 'main/applicant.html', context)

        if selected_job is None:
            messages.error(request, 'Please select the uploaded job description before submitting.')
            return render(request, 'main/applicant.html', context)

        resume_profile = None
        parse_error = None
        try:
            resume_profile = parse_resume_upload(resume_file)
        except ValueError as exc:
            parse_error = str(exc)
        finally:
            if hasattr(resume_file, 'seek'):
                resume_file.seek(0)

        applicant = Applicant.objects.create(
            first_name=first_name,
            last_name=last_name,
            contact_number=contact,
            email=email,
            resume=resume_file,
        )

        if resume_profile is not None:
            update_applicant_from_resume_profile(applicant, resume_profile)
            applicant.save(update_fields=[
                'resume_text',
                'parsed_skills',
                'parsed_education',
                'parsed_experience',
                'parsed_locations',
            ])

            matched_job, match_result = evaluate_best_job_match(
                resume_profile,
                available_jobs,
                selected_job=selected_job,
            )
            if matched_job is not None and match_result is not None:
                applicant.applied_job = matched_job
                applicant.job_preference_locked = selected_job is not None
                applicant.position = matched_job.title
                applicant.match_score = round(match_result.overall_score * 100)
                applicant.match_notes = serialize_match_result(match_result)
                applicant.save(update_fields=[
                    'applied_job',
                    'job_preference_locked',
                    'position',
                    'match_score',
                    'match_notes',
                ])

        if parse_error:
            messages.warning(request, f'Application submitted, but resume analysis was limited: {parse_error}')
        else:
            messages.success(request, 'Application submitted successfully.')
        return redirect('apply')

    return render(request, 'main/applicant.html', context)