from django.db import migrations, models


def normalize_applicant_statuses(apps, schema_editor):
    Applicant = apps.get_model('main', 'Applicant')
    Applicant.objects.filter(status='REVIEWING').update(status='NEW')
    Applicant.objects.filter(status='OFFER').update(status='INTERVIEW')


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0006_hractivitylog'),
    ]

    operations = [
        migrations.RunPython(normalize_applicant_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='applicant',
            name='status',
            field=models.CharField(
                choices=[('NEW', 'New'), ('INTERVIEW', 'For Interview')],
                default='NEW',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='historicalapplicant',
            name='status',
            field=models.CharField(
                choices=[('NEW', 'New'), ('INTERVIEW', 'For Interview')],
                default='NEW',
                max_length=20,
            ),
        ),
    ]