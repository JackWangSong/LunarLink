# Generated by Django 3.2.1 on 2024-01-12 14:12

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('lunarlink', '0010_auto_20240112_1412'),
    ]

    operations = [
        migrations.RenameField(
            model_name='api',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='case',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='casestep',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='config',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='debugtalk',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='hostip',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='project',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='report',
            old_name='modifier',
            new_name='updater',
        ),
        migrations.RenameField(
            model_name='variables',
            old_name='modifier',
            new_name='updater',
        ),
    ]
