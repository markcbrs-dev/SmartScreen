from django.db import migrations, models

import main.security


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0011_alter_historicalapplicant_resume'),
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
            field=models.TextField(max_length=512),
        ),
    ]