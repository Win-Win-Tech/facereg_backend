from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('regface', '0018_alter_assignment_shift_nullable'),
    ]

    operations = [
        migrations.AddField(
            model_name='shift',
            name='location',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='shifts', to='regface.location'),
        ),
    ]

