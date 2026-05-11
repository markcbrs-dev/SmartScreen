from pathlib import Path

from django.core.files.base import ContentFile
from django.db import migrations

import main.security


APPLICANT_FIELD_NAMES = [
    'first_name',
    'last_name',
    'contact_number',
    'email',
    'resume_text',
    'parsed_skills',
    'parsed_education',
    'parsed_experience',
    'parsed_locations',
    'match_notes',
]

HISTORICAL_APPLICANT_FIELD_NAMES = [
    'first_name',
    'last_name',
    'contact_number',
    'email',
    'resume',
    'resume_text',
    'parsed_skills',
    'parsed_education',
    'parsed_experience',
    'parsed_locations',
    'match_notes',
]


def encrypt_existing_applicant_data(apps, schema_editor):
    Applicant = apps.get_model('main', 'Applicant')
    HistoricalApplicant = apps.get_model('main', 'HistoricalApplicant')

    for applicant in Applicant.objects.all().iterator():
        update_payload = {
            field_name: getattr(applicant, field_name)
            for field_name in APPLICANT_FIELD_NAMES
        }

        if applicant.resume:
            resume_name = applicant.resume.name
            if applicant.resume.storage.exists(resume_name):
                with applicant.resume.open('rb') as resume_file:
                    plaintext = resume_file.read()
                applicant.resume.storage.delete(resume_name)
                applicant.resume.save(Path(resume_name).name, ContentFile(plaintext), save=False)
                update_payload['resume'] = applicant.resume.name

        Applicant.objects.filter(pk=applicant.pk).update(**update_payload)

    for historical in HistoricalApplicant.objects.all().iterator():
        HistoricalApplicant.objects.filter(pk=historical.pk).update(**{
            field_name: getattr(historical, field_name)
            for field_name in HISTORICAL_APPLICANT_FIELD_NAMES
        })


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0009_adminuser_hruser_alter_applicant_contact_number_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='applicant',
            name='resume',
            field=main.security.EncryptedResumeFileField(max_length=512, upload_to='resumes/'),
        ),
        migrations.AlterField(
            model_name='historicalapplicant',
            name='resume',
            field=main.security.EncryptedTextField(default=''),
        ),
        migrations.RunPython(encrypt_existing_applicant_data, migrations.RunPython.noop),
    ]