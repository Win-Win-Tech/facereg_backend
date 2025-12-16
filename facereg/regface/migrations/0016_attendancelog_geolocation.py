# Generated migration - add geolocation fields to AttendanceLog

from django.db import migrations, connection


def add_geolocation_columns(apps, schema_editor):
    table = 'regface_attendancelog'
    with connection.cursor() as cursor:
        # Check and add latitude column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'latitude'" % table)
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` ADD COLUMN `latitude` DECIMAL(9, 6) NULL" % table)
        
        # Check and add longitude column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'longitude'" % table)
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` ADD COLUMN `longitude` DECIMAL(9, 6) NULL" % table)
        
        # Check and add address column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'address'" % table)
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` ADD COLUMN `address` LONGTEXT NULL" % table)


def remove_geolocation_columns(apps, schema_editor):
    table = 'regface_attendancelog'
    with connection.cursor() as cursor:
        # Check and remove latitude column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'latitude'" % table)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` DROP COLUMN `latitude`" % table)
        
        # Check and remove longitude column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'longitude'" % table)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` DROP COLUMN `longitude`" % table)
        
        # Check and remove address column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'address'" % table)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` DROP COLUMN `address`" % table)


class Migration(migrations.Migration):

    dependencies = [
        ('regface', '0015_add_location_id_to_site'),
    ]

    operations = [
        migrations.RunPython(add_geolocation_columns, remove_geolocation_columns),
    ]

