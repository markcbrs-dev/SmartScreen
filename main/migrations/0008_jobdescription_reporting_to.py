from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0007_reduce_applicant_statuses'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicaljobdescription',
            name='reporting_to',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='jobdescription',
            name='reporting_to',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]