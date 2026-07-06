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
    home_view      — public landing page where users choose Applicant or HR
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

from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, JsonResponse
from django.urls import reverse
from html import escape
from collections import defaultdict
import base64
import re
import textwrap
from .models import Applicant, JobDescription
import os
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.password_validation import validate_password as _validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import EmailValidator
from django.views.decorators.clickjacking import xframe_options_exempt, xframe_options_sameorigin
from .services.job_parser import JobDescriptionParser
from .services.skills import normalize_employment_type
from django.conf import settings

try:
    import fitz
except ImportError:
    fitz = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

_email_validator = EmailValidator()

# Magic-byte signatures used to verify uploaded file content matches extension.
_MAGIC_PDF  = b'%PDF'
_MAGIC_DOCX = b'PK\x03\x04'  # DOCX/XLSX/ZIP share the same PK ZIP header
_INTERVIEW_MATCH_SCORE_THRESHOLD = 70

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
    get_resume_parser,
    parse_resume_upload,
    refresh_applicant_match,
    refresh_applicant_matches,
    remove_active_job_description,
    replace_active_job_description,
    score_resume_against_jobs,
    serialize_match_result,
    update_applicant_from_resume_profile,
    update_job_from_profile,
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


def _is_docx_list_paragraph(paragraph) -> bool:
    style_name = getattr(getattr(paragraph, 'style', None), 'name', '') or ''
    lowered = style_name.lower()
    if 'list bullet' in lowered or 'bullet' in lowered or 'list number' in lowered or 'number' in lowered:
        return True

    paragraph_properties = getattr(getattr(paragraph, '_p', None), 'pPr', None)
    if paragraph_properties is not None and getattr(paragraph_properties, 'numPr', None) is not None:
        return True

    return False


def _render_docx_runs_html(paragraph) -> str:
    parts: list[str] = []
    for run in getattr(paragraph, 'runs', []):
        run_text = run.text or ''
        if not run_text:
            continue
        escaped_text = escape(run_text)
        if run.bold:
            escaped_text = f'<strong>{escaped_text}</strong>'
        if run.italic:
            escaped_text = f'<em>{escaped_text}</em>'
        if run.underline:
            escaped_text = f'<u>{escaped_text}</u>'
        parts.append(escaped_text)

    if parts:
        return ''.join(parts).strip()
    return escape((paragraph.text or '').strip())


def _is_docx_bold_only_heading_paragraph(paragraph) -> bool:
    raw_text = (getattr(paragraph, 'text', '') or '').strip()
    if not raw_text:
        return False

    normalized_text = _strip_leading_list_marker(raw_text).rstrip(':').strip()
    if not normalized_text:
        return False

    has_text_run = False
    for run in getattr(paragraph, 'runs', []):
        run_text = (getattr(run, 'text', '') or '').strip()
        if not run_text:
            continue
        stripped_text = _strip_leading_list_marker(run_text).rstrip(':').strip()
        if not stripped_text:
            continue
        has_text_run = True
        if not getattr(run, 'bold', False):
            return False

    if has_text_run:
        return True

    return bool(re.fullmatch(r'\*\*(.+?)\*\*:?$', raw_text))


def _build_docx_preview_blocks(file_obj) -> list[dict[str, object]]:
    if DocxDocument is None:
        raise ValueError('DOCX support is not installed. Add python-docx to preview resume uploads.')
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)

    try:
        document = DocxDocument(file_obj)
    except Exception as exc:
        raise ValueError(f'Could not read the uploaded DOCX: {exc}') from exc

    blocks: list[dict[str, object]] = []
    list_items: list[str] = []

    def flush_list_items() -> None:
        nonlocal list_items
        if list_items:
            blocks.append({'type': 'list', 'items_html': list_items})
            list_items = []

    for paragraph in document.paragraphs:
        raw_text = (paragraph.text or '').strip()
        if not raw_text:
            flush_list_items()
            continue

        paragraph_html = _render_docx_runs_html(paragraph)
        if not paragraph_html:
            flush_list_items()
            continue

        style_name = (getattr(getattr(paragraph, 'style', None), 'name', '') or '').lower()
        if _is_docx_bold_only_heading_paragraph(paragraph):
            flush_list_items()
            heading_text = _strip_leading_list_marker(raw_text).rstrip(':').strip()
            blocks.append({'type': 'heading', 'html': _render_inline_formatting(f'**{heading_text}**:')})
            continue

        if _is_docx_list_paragraph(paragraph):
            list_items.append(paragraph_html)
            continue

        flush_list_items()
        if style_name.startswith('heading'):
            blocks.append({'type': 'heading', 'html': paragraph_html})
        else:
            blocks.append({'type': 'paragraph', 'html': paragraph_html})

    flush_list_items()

    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                cell_text = '<br>'.join(
                    escape(paragraph.text.strip())
                    for paragraph in cell.paragraphs
                    if paragraph.text.strip()
                )
                cells.append(cell_text)
            if any(cells):
                rows.append(cells)
        if rows:
            blocks.append({'type': 'table', 'rows': rows})

    return blocks


def _strip_preview_html(value: str) -> str:
    return re.sub(r'<[^>]+>', '', value or '').strip()


def _wrap_pdf_preview_text(text: str, max_chars_per_line: int) -> list[str]:
    wrapped_lines: list[str] = []
    for raw_line in (text or '').splitlines() or ['']:
        stripped = raw_line.strip()
        if not stripped:
            wrapped_lines.append('')
            continue
        wrapped_lines.extend(
            textwrap.wrap(
                stripped,
                width=max_chars_per_line,
                break_long_words=False,
                break_on_hyphens=False,
            ) or ['']
        )
    return wrapped_lines or ['']


def _draw_pdf_preview_text(page, text: str, *, x: float, y: float, width: float, font_size: float) -> float:
    plain_text = _strip_preview_html(text)
    if not plain_text:
        return y

    max_chars_per_line = max(24, int(width / max(font_size * 0.52, 1)))
    wrapped_lines = _wrap_pdf_preview_text(plain_text, max_chars_per_line)
    line_height = font_size * 1.45
    box_height = max(line_height + 4, len(wrapped_lines) * line_height + 2)
    rect = fitz.Rect(x, y, x + width, y + box_height)
    page.insert_textbox(
        rect,
        '\n'.join(wrapped_lines),
        fontsize=font_size,
        fontname='helv',
        color=(0.12, 0.16, 0.2),
        lineheight=1.15,
    )
    return y + box_height


def _build_docx_preview_pdf_bytes(file_obj) -> bytes:
    if fitz is None:
        raise ValueError('PDF preview generation is unavailable because PyMuPDF is not installed.')

    blocks = _build_docx_preview_blocks(file_obj)
    pages = _paginate_docx_preview_blocks(blocks)
    if not pages:
        return b''

    pdf_document = fitz.open()
    page_width = 595
    page_height = 842
    margin_x = 42
    margin_y = 44
    content_width = page_width - (margin_x * 2)

    try:
        for page_blocks in pages:
            page = pdf_document.new_page(width=page_width, height=page_height)
            page.draw_rect(page.rect, color=(0.85, 0.89, 0.93), fill=(1, 1, 1), width=1)
            cursor_y = margin_y

            for block in page_blocks:
                block_type = block.get('type')
                if block_type == 'heading':
                    cursor_y = _draw_pdf_preview_text(
                        page,
                        str(block.get('html', '')),
                        x=margin_x,
                        y=cursor_y,
                        width=content_width,
                        font_size=16,
                    ) + 8
                    continue

                if block_type == 'list':
                    for item in block.get('items_html', []) or []:
                        cursor_y = _draw_pdf_preview_text(
                            page,
                            f'• {_strip_preview_html(str(item))}',
                            x=margin_x + 8,
                            y=cursor_y,
                            width=content_width - 8,
                            font_size=11.5,
                        ) + 4
                    cursor_y += 4
                    continue

                if block_type == 'table':
                    for row in block.get('rows', []) or []:
                        row_text = ' | '.join(_strip_preview_html(str(cell)) for cell in row if str(cell).strip())
                        cursor_y = _draw_pdf_preview_text(
                            page,
                            row_text,
                            x=margin_x,
                            y=cursor_y,
                            width=content_width,
                            font_size=10.5,
                        ) + 6
                    cursor_y += 4
                    continue

                cursor_y = _draw_pdf_preview_text(
                    page,
                    str(block.get('html', '')),
                    x=margin_x,
                    y=cursor_y,
                    width=content_width,
                    font_size=11.5,
                ) + 6

        return pdf_document.tobytes()
    finally:
        pdf_document.close()


def _estimate_docx_preview_block_units(block: dict[str, object]) -> int:
    block_type = block.get('type')
    if block_type == 'heading':
        return 5
    if block_type == 'list':
        items = block.get('items_html', []) or []
        total_text = sum(len(re.sub(r'<[^>]+>', '', item or '')) for item in items)
        return max(6, len(items) * 3 + total_text // 180)
    if block_type == 'table':
        rows = block.get('rows', []) or []
        return max(8, len(rows) * 4)

    html = block.get('html', '') or ''
    visible_text = len(re.sub(r'<[^>]+>', '', html))
    return max(3, visible_text // 160 + 2)


def _paginate_docx_preview_blocks(blocks: list[dict[str, object]], page_unit_limit: int = 36) -> list[list[dict[str, object]]]:
    if not blocks:
        return []

    pages: list[list[dict[str, object]]] = []
    current_page: list[dict[str, object]] = []
    current_units = 0

    for block in blocks:
        block_units = _estimate_docx_preview_block_units(block)
        if current_page and current_units + block_units > page_unit_limit:
            pages.append(current_page)
            current_page = []
            current_units = 0

        current_page.append(block)
        current_units += block_units

    if current_page:
        pages.append(current_page)

    return pages


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


def _strip_leading_list_marker(line: str) -> str:
    return re.sub(r'^(?:[•*-]\s*|\d+[.)]\s*)', '', line).strip()


def _extract_bold_only_heading(line: str) -> str | None:
    candidate = _strip_leading_list_marker(line)
    match = re.fullmatch(r'\*\*(.+?)\*\*:?$', candidate)
    if not match:
        return None

    inner_text = match.group(1).strip().rstrip(':').strip()
    if not inner_text:
        return None
    return f'**{inner_text}**:'


def _is_description_heading(line: str) -> bool:
    if _extract_bold_only_heading(line):
        return True

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
            stripped_heading = _strip_leading_list_marker(line)
            heading_line = _extract_bold_only_heading(line) or (stripped_heading if stripped_heading.endswith(':') else f'{stripped_heading}:')
            current_section = {
                'heading_html': _render_inline_formatting(heading_line),
                'blocks': [],
            }
            sections.append(current_section)
            continue

        is_dash_bullet = bool(re.match(r'^-\s+', line))
        is_bullet = bool(re.match(r'^(?:[•]\s+|\*\s+)', line))
        if is_dash_bullet:
            cleaned = line.strip()
        else:
            cleaned = _strip_leading_list_marker(line)
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


def _normalize_job_description_edit_text(text: str) -> str:
    normalized_lines: list[str] = []
    for raw_line in (text or '').splitlines():
        stripped_line = raw_line.strip()
        if stripped_line and _is_description_heading(stripped_line):
            heading_line = _extract_bold_only_heading(stripped_line)
            if heading_line is not None:
                normalized_lines.append(heading_line)
                continue

            cleaned_heading = _strip_leading_list_marker(stripped_line)
            normalized_lines.append(cleaned_heading if cleaned_heading.endswith(':') else f'{cleaned_heading}:')
            continue

        normalized_lines.append(raw_line)

    return '\n'.join(normalized_lines)


def _get_job_display_text(job: JobDescription) -> str:
    if job.raw_text:
        return job.raw_text

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


def _build_match_score_display_data(match_notes, *, fallback_overall_score: int | float = 0) -> dict[str, object]:
    match_notes = match_notes if isinstance(match_notes, dict) else {}
    raw_component_scores = match_notes.get('raw_component_scores', {}) if isinstance(match_notes.get('raw_component_scores'), dict) else {}
    raw_breakdown = match_notes.get('breakdown', {}) if isinstance(match_notes.get('breakdown'), dict) else {}
    raw_component_breakdown = {
        'skill_score': raw_component_scores.get('skill_score', raw_breakdown.get('skill_score', 0)),
        'experience_score': raw_component_scores.get('experience_score', raw_breakdown.get('experience_score', 0)),
        'education_score': raw_component_scores.get('education_score', raw_breakdown.get('education_score', 0)),
    }
    score_weights = {
        'skill_score': 30,
        'experience_score': 50,
        'education_score': 20,
    }
    certification_score = raw_component_scores.get('certification_score', raw_breakdown.get('certification_score'))
    if isinstance(certification_score, (int, float)):
        raw_component_breakdown['certification_score'] = certification_score
        score_weights['certification_score'] = 20

    weighted_component_breakdown = {
        key: (raw_component_breakdown.get(key, 0) * weight) / 100
        for key, weight in score_weights.items()
    }
    match_breakdown = {
        key: round(value, 1)
        for key, value in weighted_component_breakdown.items()
    }
    uncapped_component_total = round(sum(weighted_component_breakdown.values()), 1)

    has_component_data = bool(raw_component_scores or raw_breakdown)
    if has_component_data:
        display_overall_score = round(min(100, uncapped_component_total), 1)
    else:
        display_overall_score = round(min(100, float(fallback_overall_score or 0)), 1)

    score_labels = {
        'skill_score': 'Skill Score',
        'experience_score': 'Experience Score',
        'education_score': 'Education Score',
        'certification_score': 'Certification Bonus',
    }
    score_analysis_rows = [
        {
            'key': key,
            'label': score_labels.get(key, key.replace('_', ' ').title()),
            'raw_score': round(raw_component_breakdown.get(key, 0), 1),
            'weight': weight,
            'contribution': round(weighted_component_breakdown.get(key, 0), 1),
            'is_bonus': key == 'certification_score',
        }
        for key, weight in score_weights.items()
        if key in raw_component_breakdown
    ]

    return {
        'raw_component_scores': raw_component_scores,
        'raw_breakdown': raw_breakdown,
        'raw_component_breakdown': raw_component_breakdown,
        'score_weights': score_weights,
        'match_breakdown': match_breakdown,
        'score_analysis_rows': score_analysis_rows,
        'uncapped_component_total': uncapped_component_total,
        'display_overall_score': display_overall_score,
        'score_cap_applied': uncapped_component_total > 100,
    }


def _build_apply_job_details(available_jobs: list[JobDescription]) -> list[dict[str, object]]:
    job_details = []
    for job in available_jobs:
        _about = extract_about_job_onward_text(job.display_text)
        job_details.append({
            'id': job.id,
            'title': job.title,
            'company': job.company,
            'location': job.location,
            'reporting_to': job.reporting_to,
            'employment_type': job.employment_type,
            'responsibilities': list(job.responsibilities or []),
            'description_preview_text': _about,
            'description_preview_sections': _build_description_preview_sections(_about),
        })
    return job_details


def _build_pdf_preview_images(file_bytes: bytes) -> list[str]:
    if fitz is None:
        raise ValueError('PDF preview generation is unavailable because PyMuPDF is not installed.')

    preview_document = fitz.open(stream=file_bytes, filetype='pdf')
    page_images: list[str] = []
    zoom_matrix = fitz.Matrix(1.35, 1.35)
    try:
        for page in preview_document:
            pixmap = page.get_pixmap(matrix=zoom_matrix, alpha=False)
            page_images.append(base64.b64encode(pixmap.tobytes('png')).decode('ascii'))
    finally:
        preview_document.close()
    return page_images

# -----------------------------------------------------------------------
# AUTH VIEWS
# DEBUG: check messages.error(request, ...) calls for form validation issues.
# To change session duration, edit request.session.set_expiry() in login_view.
# To change approval behaviour, see is_active=False in hr_register.
# -----------------------------------------------------------------------
def home_view(request):
    return render(request, 'main/home.html')


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
            # Show a single generic message for ALL failure cases.
            # Previously the code called pending_user.check_password() and showed
            # "Wait for Admin approval" only when credentials were correct but the
            # account was inactive — confirming to an attacker that the password
            # is valid.  Now we show the same error regardless, and only tell
            # the user to contact an admin if the account clearly exists and is
            # pending (existence alone is enough; no password confirmation needed).
            email = request.POST.get('username', '').strip()
            pending_user = User.objects.filter(
                username=email, is_active=False, is_staff=True
            ).first()
            if pending_user:
                messages.error(request, 'Your account is pending admin approval.')
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

        # Validate email format before hitting the database.
        try:
            _email_validator(email)
        except DjangoValidationError:
            messages.error(request, 'Enter a valid email address.')
            return render(request, 'main/hr_register.html')

        if User.objects.filter(username=email).exists():
            messages.error(request, 'A user with that email already exists.')
            return render(request, 'main/hr_register.html')

        # Validate password strength using Django's configured AUTH_PASSWORD_VALIDATORS.
        # Build a temporary (unsaved) User so the similarity validator can compare
        # the password against the user's name and email.
        _temp_user = User(username=email, first_name=first_name, last_name=last_name)
        try:
            _validate_password(password, user=_temp_user)
        except DjangoValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
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
    return redirect('home')

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
# Allowed values for the redirect_panel query parameter used in dashboard POST
# actions.  Any value not in this set is ignored and falls back to None.
_VALID_REDIRECT_PANELS = {'jobs', 'applicants', 'interview', 'status', 'dashboard'}


@_staff_required
def dashboard(request):
    def get_dashboard_match_score(applicant: Applicant) -> float:
        match_notes = applicant.match_notes if isinstance(applicant.match_notes, dict) else {}
        return _build_match_score_display_data(
            match_notes,
            fallback_overall_score=applicant.match_score or 0,
        )['display_overall_score']

    def redirect_to_dashboard(panel: str | None = None):
        url = reverse('dashboard')
        # Whitelist panel values to prevent arbitrary strings in the URL.
        if panel and panel in _VALID_REDIRECT_PANELS:
            url = f'{url}?panel={panel}'
        return redirect(url)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'upload_job_description':
            uploaded_job_files = [file_obj for file_obj in request.FILES.getlist('job_description_file') if file_obj]
            if not uploaded_job_files:
                messages.error(request, 'Please choose a job description file to upload.')
                return redirect_to_dashboard('jobs')

            allowed_ext = {'.txt', '.docx'}
            max_size = getattr(settings, 'MAX_JOB_DESCRIPTION_SIZE', 512 * 1024)

            for uploaded_job_file in uploaded_job_files:
                ext = os.path.splitext(uploaded_job_file.name)[1].lower()
                if ext not in allowed_ext:
                    messages.error(request, 'Unsupported job description format. Use TXT or DOCX.')
                    return redirect_to_dashboard('jobs')

                # Verify DOCX content using magic bytes (plain TXT has no signature).
                if ext == '.docx':
                    _jd_magic = uploaded_job_file.read(4)
                    uploaded_job_file.seek(0)
                    if not _jd_magic.startswith(_MAGIC_DOCX):
                        messages.error(request, 'The uploaded file does not appear to be a valid DOCX.')
                        return redirect_to_dashboard('jobs')

                if uploaded_job_file.size > max_size:
                    messages.error(request, f'Job description file too large (max {max_size // 1024} KB).')
                    return redirect_to_dashboard('jobs')

            saved_jobs = []
            try:
                for uploaded_job_file in uploaded_job_files:
                    job_result = replace_active_job_description(uploaded_job_file)
                    if isinstance(job_result, list):
                        saved_jobs.extend(job_result)
                    else:
                        saved_jobs.append(job_result)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                titles = ', '.join(job.title for job in saved_jobs[:3])
                if len(saved_jobs) > 3:
                    titles = f'{titles}, ...'
                if len(saved_jobs) == 1:
                    messages.success(request, f'Uploaded job description: {saved_jobs[0].title}.')
                else:
                    messages.success(request, f'Uploaded {len(saved_jobs)} job descriptions: {titles}.')
            return redirect_to_dashboard('jobs')

        if action == 'remove_job_description':
            job_id = request.POST.get('job_id', '').strip()
            if remove_active_job_description(job_id or None):
                messages.success(request, 'Uploaded job description removed.')
            else:
                messages.info(request, 'No uploaded job description to remove.')
            return redirect_to_dashboard('jobs')

        if action == 'update_job_description':
            job_id = request.POST.get('job_id', '').strip()
            job = JobDescription.objects.filter(id=job_id).first() if job_id else None
            if job is None:
                messages.error(request, 'Job description not found.')
                return redirect_to_dashboard('jobs')

            title = request.POST.get('title', '').strip()
            company = request.POST.get('company', '').strip()
            location = request.POST.get('location', '').strip()
            employment_type = normalize_employment_type(request.POST.get('employment_type', '').strip()) or ''
            reporting_to = request.POST.get('reporting_to', '').strip()
            raw_text = request.POST.get('raw_text', '').strip()

            if not title:
                messages.error(request, 'Job title is required.')
                return redirect_to_dashboard('jobs')
            if not raw_text:
                messages.error(request, 'Job description is required.')
                return redirect_to_dashboard('jobs')

            profile_result = _job_description_parser.parse_text(
                raw_text,
                filename=os.path.basename(job.description_file.name) if job.description_file else f'{job.title}.txt',
            )
            if isinstance(profile_result, list):
                messages.error(request, 'Edit the job description so it contains only one job posting.')
                return redirect_to_dashboard('jobs')

            job._history_user = request.user
            update_job_from_profile(job, profile_result)
            job.title = title[:200]
            job.company = company[:200]
            job.location = location[:200]
            job.employment_type = employment_type[:100]
            job.reporting_to = reporting_to[:200]
            job.save(update_fields=[
                'title',
                'company',
                'location',
                'employment_type',
                'reporting_to',
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
                'raw_text',
            ])
            Applicant.objects.filter(applied_job=job).update(position=job.title)
            messages.success(request, f'Updated job description: {job.title}.')
            return redirect_to_dashboard('jobs')

        if action == 'remove_applicant':
            applicant_id = request.POST.get('applicant_id', '').strip()
            redirect_panel = request.POST.get('redirect_panel', 'interview').strip()
            applicant = Applicant.objects.filter(id=applicant_id).first() if applicant_id else None
            if applicant is None:
                messages.error(request, 'Applicant not found.')
            else:
                applicant_name = applicant.get_full_name()
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
                messages.success(request, f'{applicant.get_full_name()} moved to "{label}".')
            return redirect_to_dashboard(redirect_panel)

    jobs = get_active_job_descriptions()
    for job in jobs:
        job.employment_type = normalize_employment_type(job.employment_type) or ''
        job.display_text = _get_job_display_text(job)
        job.editable_raw_text = _normalize_job_description_edit_text(job.raw_text or '')
        if not getattr(job, 'reporting_to', ''):
            job.reporting_to = _job_description_parser.extract_reporting_to(job.display_text)
        job.description_text = extract_job_description_section(job.display_text)
        job.description_preview_text = extract_description_preview_text(job.display_text)
        _about_text = extract_about_job_onward_text(job.display_text)  # compute once; reused for both preview variants
        job.description_preview_sections = _build_description_preview_sections(_about_text)
        job.description_preview_excerpt_sections = _build_dashboard_preview_sections(_about_text)
    active_job = jobs[0] if jobs else None
    applicants = list(Applicant.objects.select_related('applied_job').all().order_by('-created_at'))
    applicants = refresh_applicant_matches(applicants, jobs)

    grouped_applicants: dict[tuple[str, object], list[Applicant]] = defaultdict(list)
    for applicant in applicants:
        applicant.dashboard_match_score = get_dashboard_match_score(applicant)
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
        sorted_group = sorted(group_items, key=lambda a: (-(a.dashboard_match_score or 0), a.created_at))
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
    for applicant in applicants:
        applicant.resume_download_url = None
        if applicant.resume:
            applicant.resume_download_url = reverse('serve_resume', args=[os.path.basename(applicant.resume.name)])

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


@_staff_required
def applicant_detail(request, applicant_id):
    applicant = get_object_or_404(Applicant.objects.select_related('applied_job'), id=applicant_id)

    applicant, _ = refresh_applicant_match(applicant, get_active_job_descriptions())
    applicant.resume_download_url = None
    applicant.resume_preview_url = None
    if applicant.resume:
        applicant.resume_download_url = reverse('serve_resume', args=[os.path.basename(applicant.resume.name)])
        applicant.resume_preview_url = reverse('applicant_resume_preview', args=[applicant.id])

    resume_name = os.path.basename(applicant.resume.name) if applicant.resume else ''
    resume_extension = os.path.splitext(resume_name)[1].lower()
    match_notes = applicant.match_notes if isinstance(applicant.match_notes, dict) else {}
    score_display = _build_match_score_display_data(
        match_notes,
        fallback_overall_score=applicant.match_score or 0,
    )
    matched_job = match_notes.get('matched_job', {}) if isinstance(match_notes.get('matched_job'), dict) else {}
    matched_job_id = matched_job.get('job_id') if isinstance(matched_job.get('job_id'), int) else None
    job_position_scores = match_notes.get('all_job_scores', []) if isinstance(match_notes.get('all_job_scores'), list) else []
    if not job_position_scores:
        fallback_scores = []
        seen_job_ids = set()
        overall_score = match_notes.get('overall_score')
        if isinstance(matched_job.get('job_id'), int) and isinstance(matched_job.get('job_title'), str) and isinstance(overall_score, int):
            seen_job_ids.add(matched_job['job_id'])
            fallback_scores.append({
                'job_id': matched_job['job_id'],
                'job_title': matched_job['job_title'],
                'overall_score': overall_score,
            })

        recommendations = match_notes.get('recommendations', []) if isinstance(match_notes.get('recommendations'), list) else []
        for item in recommendations:
            if not isinstance(item, dict):
                continue
            job_id = item.get('job_id')
            job_title = item.get('job_title')
            recommendation_score = item.get('overall_score')
            if not isinstance(job_id, int) or not isinstance(job_title, str) or not isinstance(recommendation_score, int):
                continue
            if job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            fallback_scores.append({
                'job_id': job_id,
                'job_title': job_title,
                'overall_score': recommendation_score,
            })

        job_position_scores = sorted(
            fallback_scores,
            key=lambda item: (-item['overall_score'], item['job_title'].lower()),
        )

    if matched_job_id is not None:
        job_position_scores = [
            item for item in job_position_scores
            if isinstance(item, dict) and item.get('job_id') != matched_job_id
        ]

    context = {
        'applicant': applicant,
        'resume_name': resume_name,
        'resume_extension': resume_extension,
        'resume_is_pdf': resume_extension == '.pdf',
        'display_overall_score': score_display['display_overall_score'],
        'uncapped_component_total': score_display['uncapped_component_total'],
        'score_cap_applied': score_display['score_cap_applied'],
        'match_breakdown': score_display['match_breakdown'],
        'score_analysis_rows': score_display['score_analysis_rows'],
        'matching_skills': match_notes.get('matching_skills', []),
        'missing_skills': match_notes.get('missing_skills', []),
        'recommendation': match_notes.get('recommendation', {}),
        'recommendations': match_notes.get('recommendations', []),
        'job_position_scores': job_position_scores,
        'matched_job': matched_job,
    }
    return render(request, 'main/applicant_detail.html', context)


@_staff_required
@xframe_options_exempt
def applicant_resume_preview(request, applicant_id):
    applicant = get_object_or_404(Applicant.objects.only('id', 'resume', 'resume_text', 'first_name', 'middle_name', 'last_name'), id=applicant_id)
    if not applicant.resume:
        raise Http404

    resume_name = os.path.basename(applicant.resume.name)
    resume_extension = os.path.splitext(resume_name)[1].lower()
    resume_source_url = reverse('serve_resume', args=[resume_name])
    preview_context = {
        'applicant': applicant,
        'resume_name': resume_name,
        'resume_extension': resume_extension,
        'resume_source_url': resume_source_url,
        'pdf_page_images': [],
        'pdf_preview_fallback': False,
        'docx_preview_pages': [],
        'docx_preview_blocks': [],
        'preview_text': applicant.resume_text or '',
    }

    if resume_extension == '.pdf':
        if not applicant.resume.storage.exists(applicant.resume.name):
            raise Http404
        try:
            with applicant.resume.open('rb') as resume_handle:
                preview_context['pdf_page_images'] = _build_pdf_preview_images(resume_handle.read())
        except (OSError, ValueError):
            preview_context['pdf_preview_fallback'] = True
        return render(request, 'main/applicant_resume_preview.html', preview_context)

    if resume_extension == '.docx':
        if not applicant.resume.storage.exists(applicant.resume.name):
            raise Http404
        preview_context['preview_text'] = ''
        return render(request, 'main/applicant_resume_preview.html', preview_context)

    if not applicant.resume_text:
        raise Http404

    return render(request, 'main/applicant_resume_preview.html', preview_context)


# -----------------------------------------------------------------------
# PROTECTED RESUME DOWNLOAD
# Resumes contain PII (name, contact, address). This view gates access so
# only authenticated staff can download them. Direct /media/resumes/ URLs
# are NOT served in production (DEBUG=False removes the static() helper).
# To allow access: user must be active staff.
# -----------------------------------------------------------------------
@_staff_required
@xframe_options_sameorigin
def serve_resume(request, filename):
    """Serve a resume file to authenticated staff only.

    Sanitizes the filename to prevent path traversal (e.g. ../../etc/passwd).
    Only serves files that actually belong to a known Applicant record.
    """
    safe_name = os.path.basename(filename)  # strip any directory components
    applicant = next(
        (
            candidate
            for candidate in Applicant.objects.all().only('id', 'resume')
            if candidate.resume and os.path.basename(candidate.resume.name) == safe_name
        ),
        None,
    )
    if applicant is None or not applicant.resume:
        raise Http404
    if not applicant.resume.storage.exists(applicant.resume.name):
        raise Http404
    return FileResponse(applicant.resume.open('rb'), as_attachment=False, filename=safe_name)


def applicant_job_details(request):
    available_jobs = get_active_job_descriptions()
    for job in available_jobs:
        job.employment_type = normalize_employment_type(job.employment_type) or ''
        job.display_text = _get_job_display_text(job)

    payload = {
        'jobs': _build_apply_job_details(available_jobs),
        'has_active_job': bool(available_jobs),
    }
    return JsonResponse(payload)

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
    # Compute display_text once per job so the file is not read multiple times
    # (previously _get_job_display_text was called 2–3× per job inside the loop/comprehension).
    for job in available_jobs:
        job.employment_type = normalize_employment_type(job.employment_type) or ''
        job.display_text = _get_job_display_text(job)
    active_job = available_jobs[0] if available_jobs else None
    if active_job is not None:
        active_job.description_preview_text = extract_about_job_onward_text(active_job.display_text)
        active_job.description_preview_sections = _build_description_preview_sections(active_job.description_preview_text)
    job_details = _build_apply_job_details(available_jobs)
    context = {
        'available_jobs': available_jobs,
        'active_job': active_job,
        'job_details': job_details,
    }
    if request.method == 'POST':
        resume_file = request.FILES.get('resume')
        selected_job_id = request.POST.get('job_id', '').strip()

        if not resume_file:
            messages.error(request, 'Please upload a resume file.')
            return render(request, 'main/applicant.html', context)

        allowed_ext = {'.pdf', '.docx'}
        ext = os.path.splitext(resume_file.name)[1].lower()
        if ext not in allowed_ext:
            messages.error(request, 'Unsupported file type. Use PDF or DOCX.')
            return render(request, 'main/applicant.html', context)

        # Validate file content against its declared extension using magic bytes.
        # A malicious file named "shell.pdf" containing a script would fail this check.
        _magic = resume_file.read(4)
        resume_file.seek(0)
        if ext == '.pdf' and not _magic.startswith(_MAGIC_PDF):
            messages.error(request, 'The uploaded file does not appear to be a valid PDF.')
            return render(request, 'main/applicant.html', context)
        if ext == '.docx' and not _magic.startswith(_MAGIC_DOCX):
            messages.error(request, 'The uploaded file does not appear to be a valid DOCX.')
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

        full_name = ''
        if resume_profile is not None:
            full_name = next(
                (line.strip() for line in (resume_profile.full_name or '').splitlines() if line.strip()),
                '',
            )
        contact = (resume_profile.phone or '').strip() if resume_profile is not None else ''
        email = (resume_profile.email or '').strip() if resume_profile is not None else ''

        missing_resume_fields = []
        if not full_name:
            missing_resume_fields.append('name')
        if not contact:
            missing_resume_fields.append('contact number')
        if not email:
            missing_resume_fields.append('email')
        if parse_error or missing_resume_fields:
            missing_label = ', '.join(missing_resume_fields)
            if parse_error:
                messages.error(request, f'We could not extract your name, contact number, and email from the uploaded resume: {parse_error}')
            else:
                messages.error(request, f'We could not extract the following from the uploaded resume: {missing_label}.')
            return render(request, 'main/applicant.html', context)

        first_name, middle_name, last_name = get_resume_parser().split_name_components(full_name)
        if not first_name and not last_name:
            name_parts = full_name.split(None, 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ''
            middle_name = ''

        if len(first_name) > 100 or len(middle_name) > 100 or len(last_name) > 100:
            first_name = first_name[:100]
            middle_name = middle_name[:100]
            last_name = last_name[:100]

        if email:
            try:
                _email_validator(email)
            except DjangoValidationError:
                email = ''

        applicant = Applicant.objects.create(
            first_name=first_name,
            middle_name=middle_name,
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
                scored_jobs = score_resume_against_jobs(resume_profile, available_jobs)
                recommendation_job = None
                recommendation_result = None
                for job, result in scored_jobs:
                    if job.id == matched_job.id:
                        continue
                    if recommendation_result is None or result.overall_score > recommendation_result.overall_score:
                        recommendation_job = job
                        recommendation_result = result

                applicant.applied_job = matched_job
                applicant.job_preference_locked = selected_job is not None
                applicant.position = matched_job.title
                applicant.match_score = round(match_result.overall_score * 100)
                applicant.status = 'INTERVIEW' if applicant.match_score >= _INTERVIEW_MATCH_SCORE_THRESHOLD else 'NEW'
                applicant.match_notes = serialize_match_result(
                    match_result,
                    matched_job=matched_job,
                    recommendation_job=recommendation_job,
                    recommendation_result=recommendation_result,
                    scored_jobs=scored_jobs,
                )
                applicant.save(update_fields=[
                    'applied_job',
                    'job_preference_locked',
                    'position',
                    'status',
                    'match_score',
                    'match_notes',
                ])

        messages.success(request, 'Application submitted successfully.')
        return redirect('apply')

    return render(request, 'main/applicant.html', context)