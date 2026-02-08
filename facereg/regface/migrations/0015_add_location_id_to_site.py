from django.db import migrations, connection


def add_location_column(apps, schema_editor):
    table = 'regface_site'
    with connection.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'location_id'" % table)
        if cursor.fetchone():
            return
        cursor.execute("ALTER TABLE `%s` ADD COLUMN `location_id` varchar(36) NULL" % table)
        cursor.execute(
            "ALTER TABLE `%s` ADD CONSTRAINT `regface_site_location_id_fk` FOREIGN KEY (`location_id`) REFERENCES `regface_location` (`id`) ON DELETE CASCADE" % table
        )


def remove_location_column(apps, schema_editor):
    table = 'regface_site'
    with connection.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'location_id'" % table)
        if not cursor.fetchone():
            return
        try:
            cursor.execute("ALTER TABLE `%s` DROP FOREIGN KEY `regface_site_location_id_fk`" % table)
        except Exception:
            pass
        cursor.execute("ALTER TABLE `%s` DROP COLUMN `location_id`" % table)


class Migration(migrations.Migration):

    dependencies = [
        ('regface', '0014_employee_aadhaar_number_employee_address_and_more'),
    ]

    operations = [
        migrations.RunPython(add_location_column, remove_location_column),
    ]
