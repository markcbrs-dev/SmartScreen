from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from .models import Applicant, JobDescription
import os
from pathlib import Path
from django.contrib.auth.decorators import login_required

from .services import (
    build_resume_profile_from_applicant,
    evaluate_best_job_match,
    get_active_job_descriptions,
    parse_resume_upload,
    refresh_applicant_matches,
    serialize_match_result,
    sync_sample_job_description,
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
        user.save()

        messages.success(request, 'Registration successful. You can now log in.')
        return redirect('login')

    return render(request, 'main/hr_register.html')


@login_required
def dashboard(request):
    active_job = sync_sample_job_description()
    applicants = list(Applicant.objects.select_related('applied_job').all().order_by('-created_at'))
    applicants = refresh_applicant_matches(applicants, active_job)
    jobs = [active_job] if active_job is not None else []
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


def apply(request):
    active_job = sync_sample_job_description()
    available_jobs = [active_job] if active_job is not None else []
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        contact = request.POST.get('contact', request.POST.get('contact_number', '')).strip()
        email = request.POST.get('email', '').strip()
        resume_file = request.FILES.get('resume')
        selected_job_id = request.POST.get('job_id', '').strip()

        if not first_name or not last_name or not contact or not email:
            messages.error(request, 'Please fill in all required fields.')
            return render(request, 'main/applicant.html', {'available_jobs': available_jobs})

        if not resume_file:
            messages.error(request, 'Please upload a resume file.')
            return render(request, 'main/applicant.html', {'available_jobs': available_jobs})

        allowed_ext = {'.pdf', '.docx'}
        ext = os.path.splitext(resume_file.name)[1].lower()
        if ext not in allowed_ext:
            messages.error(request, 'Unsupported file type. Use PDF or DOCX.')
            return render(request, 'main/applicant.html', {'available_jobs': available_jobs})

        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
        if resume_file.size > max_size:
            messages.error(request, f'Resume file too large (max {max_size // 1024 // 1024} MB).')
            return render(request, 'main/applicant.html', {'available_jobs': available_jobs})

        selected_job = None
        if active_job is not None and selected_job_id == str(active_job.id):
            selected_job = active_job

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
        elif applicant.position:
            messages.success(request, f'Application submitted successfully. Best match: {applicant.position} ({applicant.match_score}%).')
        else:
            messages.success(request, 'Application submitted successfully.')
        return redirect('apply')

    return render(request, 'main/applicant.html', {'available_jobs': available_jobs})