from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
SITE_PACKAGES = ROOT / '.venv' / 'Lib' / 'site-packages'

if str(SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(SITE_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smscrweb.settings')

import django
from django.core.management import call_command


django.setup()
result_path = ROOT / '.tmp' / 'middle_name_test_result.txt'

try:
    call_command(
        'test',
        'main.tests.test_parsers.ResumeParserExtractionTests.test_split_name_components_separates_middle_initial_from_last_name',
        'main.tests.test_views.ApplicantFlowTests.test_apply_stores_middle_initial_separately_from_last_name',
        verbosity=1,
    )
except Exception as exc:
    result_path.write_text(f'FAIL\n{exc!r}\n', encoding='utf-8')
    raise
else:
    result_path.write_text('PASS\n', encoding='utf-8')