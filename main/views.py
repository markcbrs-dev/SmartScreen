from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.conf import settings
from django.contrib import messages
from .models import Resume, Details, Applicant
import os


def home(response):
    return render(response, "mainpage.html", {"name": "test"})


def apply(request):
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        contact = request.POST.get('contact_number', '').strip()
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