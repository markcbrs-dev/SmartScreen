from django.db import migrations

import main.security


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0012_alter_applicant_position_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='applicant',
            name='middle_name',
            field=main.security.EncryptedTextField(default=''),
        ),
        migrations.AddField(
            model_name='historicalapplicant',
            name='middle_name',
            field=main.security.EncryptedTextField(default=''),
        ),
    ]