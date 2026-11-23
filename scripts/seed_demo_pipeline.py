"""Seed synthetic data through the real API. --evaluate uses genuine GroqCloud only."""
import argparse
import json
import os
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluate', action='store_true')
    parser.add_argument('--approve-first', action='store_true', help='Pause for human review of the first genuine synthetic evaluation')
    args = parser.parse_args()
    if args.approve_first and not args.evaluate:
        parser.error('--approve-first requires --evaluate')
    get_settings()  # Load .env; never invent missing credentials or provider results.
    password = os.getenv('DEMO_PASSWORD')
    if not password:
        raise SystemExit('Set DEMO_PASSWORD to the seeded recruiter password first.')
    with httpx.Client(base_url=os.getenv('API_BASE_URL', 'http://127.0.0.1:8000'), timeout=180) as api:
        response = api.post('/api/auth/login', json={'email': os.getenv('DEMO_EMAIL', 'recruiter@demo.test'), 'password': password})
        if response.status_code != 200:
            raise SystemExit('Demo login failed; check the seeded account and DEMO_PASSWORD.')
        api.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
        existing = api.get('/api/requisitions', params={'limit': 100}).json()
        job_ids = []
        for job in json.loads((ROOT / 'sample_data' / 'jobs.json').read_text(encoding='utf-8')):
            found = next((item for item in existing if item['job_title'] == job['job_title']), None)
            response = api.post('/api/requisitions', json=job) if found is None else None
            if response is not None:
                response.raise_for_status()
            job_ids.append(found['requisition_id'] if found else response.json()['requisition_id'])
        paths = sorted((ROOT / 'sample_data' / 'synthetic_resumes').glob('*'))
        paths += sorted((ROOT / 'sample_data' / 'phase2_resumes').glob('*.docx'))
        accepted = []
        for index, path in enumerate(paths):
            if path.suffix.lower() not in {'.pdf', '.docx'}:
                continue
            response = api.post(f'/api/requisitions/{job_ids[index % 3]}/resumes', files={'files': (path.name, path.read_bytes(), 'application/octet-stream')})
            response.raise_for_status()
            for item in response.json()['results']:
                print(path.name, item['outcome'])
                if item['outcome'] == 'accepted':
                    accepted.append(item['application_id'])
                elif item['outcome'] == 'duplicate' and item.get('duplicate_of_application_id'):
                    accepted.append(item['duplicate_of_application_id'])
        approved = False
        if args.evaluate:
            for app_id in accepted:
                response = api.post(f'/api/applications/{app_id}/evaluate', json={})
                if response.status_code != 200:
                    print('Evaluation unavailable; no score was fabricated. Inspect GroqCloud configuration and application history.')
                    continue
                evaluation = response.json()['evaluation']
                print('Genuine evaluation saved:', evaluation['evaluation_id'])
                if args.approve_first and not approved:
                    print(json.dumps(evaluation, indent=2, default=str))
                    confirmation = input('Review the evidence above. Type APPROVE to progress this synthetic application: ')
                    if confirmation != 'APPROVE':
                        print('No shortlist decision was submitted.')
                        args.approve_first = False
                        continue
                    result = api.post(f'/api/applications/{app_id}/shortlist-decision', json={
                        'evaluation_id': evaluation['evaluation_id'], 'decision': 'approve',
                        'reason': 'Explicit operator approval of synthetic classroom evidence.'})
                    result.raise_for_status()
                    approved = True
        print('Synthetic intake complete. Pending and approved examples require genuine evaluations and explicit --approve-first.')


if __name__ == '__main__':
    main()
