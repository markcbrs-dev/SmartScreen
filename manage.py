#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # Load environment variables from .env (if it exists) before Django reads settings.
    # This lets developers set SECRET_KEY, DJANGO_DEBUG, etc. in a local .env file
    # without exporting them manually. The .env file is git-ignored.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed; env vars must be set another way

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smscrweb.settings')
    os.environ.setdefault('SMARTSCREEN_ENABLE_SPACY', '1')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
