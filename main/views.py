from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from .models import Applicant, JobDescription
import os
from django.contrib.auth.decorators import login_required

from .services import (
    build_resume_profile_from_applicant,
    evaluate_best_job_match,
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
            messages.error(request, 'Please correct the errors below.')
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

@login_required
def logout_view(request):
    if request.method != 'POST':
        return redirect('dashboard')
    auth_logout(request)
    messages.success(request, 'Logged out successfully.')
    return redirect('login')

@login_required
def dashboard(request):
    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'upload_job_description':
            uploaded_job_file = request.FILES.get('job_description_file')
            if not uploaded_job_file:
                messages.error(request, 'Please choose a job description file to upload.')
                return redirect('dashboard')

            allowed_ext = {'.txt', '.docx'}
            ext = os.path.splitext(uploaded_job_file.name)[1].lower()
            if ext not in allowed_ext:
                messages.error(request, 'Unsupported job description format. Use TXT or DOCX.')
                return redirect('dashboard')

            max_size = getattr(settings, 'MAX_JOB_DESCRIPTION_SIZE', 512 * 1024)
            if uploaded_job_file.size > max_size:
                messages.error(request, f'Job description file too large (max {max_size // 1024} KB).')
                return redirect('dashboard')

            try:
                job = replace_active_job_description(uploaded_job_file)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f'Uploaded job description: {job.title}.')
            return redirect('dashboard')

        if action == 'remove_job_description':
            job_id = request.POST.get('job_id', '').strip()
            if remove_active_job_description(job_id or None):
                messages.success(request, 'Uploaded job description removed.')
            else:
                messages.info(request, 'No uploaded job description to remove.')
            return redirect('dashboard')

    jobs = get_active_job_descriptions()
    active_job = jobs[0] if jobs else None
    applicants = list(Applicant.objects.select_related('applied_job').all().order_by('-created_at'))
    applicants = refresh_applicant_matches(applicants, active_job)
    total = len(applicants)
    new_count = sum(1 for applicant in applicants if applicant.status == 'NEW')
    reviewing_count = sum(1 for applicant in applicants if applicant.status == 'REVIEWING')
    interview_count = sum(1 for applicant in applicants if applicant.status == 'INTERVIEW')
    offers_count = sum(1 for applicant in applicants if applicant.status == 'OFFER')
    context = {
        'applicants': applicants,
        'jobs': jobs,
        'active_job': active_job,
        'total': total,
        'new_count': new_count,
        'reviewing_count': reviewing_count,
        'interview_count': interview_count,
        'offers_count': offers_count,
    }
    return render(request, 'main/dashboard.html', context)

# --- NEW VIEW FOR AUDIT LOG ---
@login_required
def audit_log_view(request):
    # This fetches the 50 most recent changes tracked by simple-history
    logs = Applicant.history.all().order_by('-history_date')[:50]
    return render(request, 'main/audit_log.html', {'logs': logs})

def apply(request):
    available_jobs = get_active_job_descriptions()
    active_job = available_jobs[0] if available_jobs else None
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
                'experience_level': job.experience_level,
                'remote_option': job.remote_option,
                'required_skills': list(job.required_skills or []),
                'responsibilities': list(job.responsibilities or []),
                'qualifications': list(job.qualifications or []),
                'education_requirements': list(job.education_requirements or []),
                'raw_text': job.raw_text or '',
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