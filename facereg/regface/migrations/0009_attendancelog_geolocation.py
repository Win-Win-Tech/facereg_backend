# Generated migration for adding geolocation fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('regface', '0008_location_alter_payrollrecord_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancelog',
            name='latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='attendancelog',
            name='longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
    ]
