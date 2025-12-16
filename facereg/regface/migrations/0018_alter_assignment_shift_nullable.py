from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('regface', '0017_remove_location_columns'),
    ]

    operations = [
        migrations.AlterField(
            model_name='assignment',
            name='shift',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, to='regface.shift'),
        ),
    ]
