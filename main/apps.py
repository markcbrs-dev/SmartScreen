"""
main/apps.py — Django app configuration for the main app.

HOW TO MODIFY
-------------
  Change the default primary key type → edit default_auto_field.
  Run startup code on app load        → override the ready() method here.
  Change the app's display name       → add a verbose_name attribute.
"""

from django.apps import AppConfig


class MainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'main'
