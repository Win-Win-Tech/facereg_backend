# Migration to remove location_address and location_name columns if they exist

from django.db import migrations, connection


def remove_location_columns(apps, schema_editor):
    """Remove location_address and location_name columns from Location model if they exist"""
    table = 'regface_location'
    with connection.cursor() as cursor:
        # Check and remove location_address column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'location_address'" % table)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` DROP COLUMN `location_address`" % table)
        
        # Check and remove location_name column
        cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'location_name'" % table)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE `%s` DROP COLUMN `location_name`" % table)


def add_location_columns_reverse(apps, schema_editor):
    """Reverse migration - add columns back if needed"""
    # This is optional; if you want to be able to reverse, uncomment below
    # table = 'regface_location'
    # with connection.cursor() as cursor:
    #     cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'location_address'" % table)
    #     if not cursor.fetchone():
    #         cursor.execute("ALTER TABLE `%s` ADD COLUMN `location_address` VARCHAR(255) NULL" % table)
    #     
    #     cursor.execute("SHOW COLUMNS FROM `%s` LIKE 'location_name'" % table)
    #     if not cursor.fetchone():
    #         cursor.execute("ALTER TABLE `%s` ADD COLUMN `location_name` VARCHAR(255) NULL" % table)
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('regface', '0016_attendancelog_geolocation'),
    ]

    operations = [
        migrations.RunPython(remove_location_columns, add_location_columns_reverse),
    ]
