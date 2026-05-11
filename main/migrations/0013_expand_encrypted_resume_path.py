from django.db import migrations, models

import main.security


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0012_alter_applicant_position_and_more'),
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