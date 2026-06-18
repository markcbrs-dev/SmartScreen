from pathlib import Path

Path('.tmp/python_smoke_check_result.txt').write_text('ok\n', encoding='utf-8')