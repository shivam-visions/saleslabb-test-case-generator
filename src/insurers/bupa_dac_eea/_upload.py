"""Upload PROD-1968 smoke CSV to Confluence.

Reads Atlassian credentials from /home/support/Desktop/MIRO/SaleslabbProject/.env
(CONFLUENCE_USER_EMAIL + CONFLUENCE_API_TOKEN), then dispatches to the shared
confluence_uploader.
"""
import os
import sys
from pathlib import Path

ROOT = Path('/home/support/Desktop/MIRO')
TICKET_DIR = (
    ROOT / 'auto-onboarding'
    / 'PROD-1968-PlanUpdate–BUPADAC(Global Health)–ROW'
)
CSV_PATH = TICKET_DIR / 'PROD-1968-output' / 'test-cases' / 'quote-scenario.csv'
MANIFEST_PATH = TICKET_DIR / 'PROD-1968-output' / 'test-cases' / 'test-cases.manifest.json'


def _load_env_from(envfile: Path):
    if not envfile.is_file():
        return
    for line in envfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main():
    _load_env_from(ROOT / 'SaleslabbProject' / '.env')
    # The shared uploader expects ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN env names
    os.environ.setdefault('ATLASSIAN_EMAIL',
                          os.environ.get('CONFLUENCE_USER_EMAIL', ''))
    os.environ.setdefault('ATLASSIAN_API_TOKEN',
                          os.environ.get('CONFLUENCE_API_TOKEN', ''))
    os.environ.setdefault('ATLASSIAN_API_TOKEN_EXPIRES', '2027-05-19')

    sys.argv = [
        'confluence_uploader',
        '--csv', str(CSV_PATH),
        '--manifest', str(MANIFEST_PATH),
        '--space', 'IPRB',
        '--test-cases-folder-id', '420544515',
        '--subfolder-title', 'ROW Test',
        '--page-title', 'BUPA DAC (Global Health) EEA- PID-C4A6E',
        '--ticket-id', 'PROD-1968',
        '--product-label', 'BUPA DAC (Global Health) EEA',
    ]
    from src.common.confluence_uploader import main as upload_main
    upload_main()


if __name__ == '__main__':
    main()
