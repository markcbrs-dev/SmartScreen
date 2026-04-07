from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from .models import Resume, Details, Applicant
import os
from django.contrib.auth.decorators import login_required


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
            return redirect('home')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        if request.user.is_authenticated:
            return redirect('home')
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
    applicants = Applicant.objects.all().order_by('-created_at')
    total = applicants.count()
    new_count = applicants.filter(status='NEW').count()
    interview_count = applicants.filter(status='INTERVIEW').count()
    offers_count = applicants.filter(status='OFFER').count()
    context = {
        'applicants': applicants,
        'total': total,
        'new_count': new_count,
        'interview_count': interview_count,
        'offers_count': offers_count,
    }
    return render(request, 'main/dashboard.html', context)


def apply(request):
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        contact = request.POST.get('contact', request.POST.get('contact_number', '')).strip()
        email = request.POST.get('email', '').strip()
        resume_file = request.FILES.get('resume')

        if not resume_file:
            messages.error(request, 'Please upload a resume file.')
            return render(request, 'main/applicant.html')

        allowed_ext = {'.pdf', '.doc', '.docx'}
        ext = os.path.splitext(resume_file.name)[1].lower()
        if ext not in allowed_ext:
            messages.error(request, 'Unsupported file type. Use PDF, DOC, or DOCX.')
            return render(request, 'main/applicant.html')

        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
        if resume_file.size > max_size:
            messages.error(request, f'Resume file too large (max {max_size // 1024 // 1024} MB).')
            return render(request, 'main/applicant.html')

        applicant = Applicant.objects.create(
            first_name=first_name,
            last_name=last_name,
            contact_number=contact,
            email=email,
            resume=resume_file,
        )

        messages.success(request, 'Application submitted successfully.')
        return redirect('home')

    return render(request, 'main/applicant.html')