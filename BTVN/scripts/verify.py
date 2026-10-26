"""Run inside user container: docker compose exec -T user python scripts/verify.py."""
import base64
import concurrent.futures
import csv
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import jwt
import requests
from minio import Minio
from app import SECRET

BASE = 'http://gateway:8080'
OUT = Path('/app/results')
OUT.mkdir(exist_ok=True)
session = requests.Session()
checks = []


def check(name, condition, detail):
    checks.append({'test': name, 'pass': bool(condition), 'detail': detail})
    print(('PASS' if condition else 'FAIL') + ': ' + name, flush=True)


def call(path, **kwargs):
    # No-burst gateway needs >=100ms between normal requests from one IP.
    time.sleep(.15)
    return session.request(kwargs.pop('method', 'GET'), BASE + path, timeout=15, **kwargs)


def main():
    for _ in range(60):
        try:
            r = call('/auth/login', method='POST', json={'email': 'an@example.com', 'password': 'demo123'})
            if r.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Gateway not ready after 60 attempts')
    token = r.json()['access_token']
    headers = {'Authorization': 'Bearer ' + token}
    check('Missing JWT -> 401', call('/users').status_code == 401, 'No Authorization header')
    check('Forged JWT -> 401', call('/users', headers={'Authorization': 'Bearer fake'}).status_code == 401, 'Invalid token')
    claims = jwt.decode(token, SECRET, algorithms=['HS256'], audience='btvn-api')
    claims['exp'] = int(time.time()) - 10
    expired = jwt.encode(claims, SECRET, algorithm='HS256')
    check('Expired JWT -> 401', call('/users', headers={'Authorization': 'Bearer ' + expired}).status_code == 401, 'Expired signed token')
    claims['exp'] = int(time.time()) + 3600
    wrong_signature = jwt.encode(claims, 'different-signing-secret-at-least-32-characters', algorithm='HS256')
    check('Wrong JWT signature -> 401', call('/users', headers={'Authorization': 'Bearer ' + wrong_signature}).status_code == 401, 'Valid format, incorrect signing key')
    u = call('/users', headers=headers)
    o = call('/orders', headers=headers)
    check('Routing /users', u.status_code == 200 and u.json()['service'] == 'user', u.text)
    check('Routing /orders', o.status_code == 200 and o.json()['service'] == 'order', o.text)
    spoof = call('/users', headers={**headers, 'X-User-ID': '2'})
    check('Gateway overwrites forged identity', spoof.status_code == 200 and spoof.json()['user']['id'] == '1', spoof.text)
    check('Other user is forbidden', call('/users/2', headers=headers).status_code == 403, '/users/2 with user 1 token')
    d = call('/dashboard', headers=headers)
    check('Dashboard aggregation', d.status_code == 200 and d.json()['summary'] == {'order_count': 2, 'total': 700000}
          and d.json()['user']['id'] == '1' and all(x['user_id'] == '1' for x in d.json()['orders']), d.text)
    signed = call('/users/avatar/upload-url', method='POST', headers=headers, json={'content_type': 'image/png'})
    signed.raise_for_status()
    info = signed.json()
    url = info['upload_url']
    check('URL expires after 300 seconds', parse_qs(urlsplit(url).query).get('X-Amz-Expires') == ['300'], 'X-Amz-Expires=300')
    # Container connects internally, retaining original signed Host header.
    parts = urlsplit(url)
    internal_url = urlunsplit((parts.scheme, 'minio:9000', parts.path, parts.query, ''))
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=')
    upload = requests.put(internal_url, data=png, headers={'Host': parts.netloc, 'Content-Type': 'image/png'}, timeout=15)
    check('Direct upload to MinIO', upload.status_code == 200, f'HTTP {upload.status_code}; key={info["object_key"]}')
    anonymous = requests.get('http://minio:9000' + parts.path, timeout=10)
    check('Bucket remains private', anonymous.status_code == 403, f'Anonymous GET: {anonymous.status_code}')
    changed_key = urlunsplit((parts.scheme, 'minio:9000', parts.path + '.other', parts.query, ''))
    changed = requests.put(changed_key, data=png, headers={'Host': parts.netloc}, timeout=10)
    check('Valet key cannot change object path', changed.status_code == 403, f'Modified path: {changed.status_code}')
    signer = Minio(parts.netloc, access_key=os.getenv('MINIO_ACCESS_KEY', 'labadmin'),
                   secret_key=os.getenv('MINIO_SECRET_KEY', 'lab-password-12345'), secure=False, region='us-east-1')
    past_url = signer.get_presigned_url('PUT', 'avatars', info['object_key'], expires=timedelta(seconds=300),
                                       request_date=datetime.now(timezone.utc) - timedelta(minutes=10))
    past = urlsplit(past_url)
    expired_upload = requests.put(urlunsplit((past.scheme, 'minio:9000', past.path, past.query, '')),
                                  data=png, headers={'Host': past.netloc}, timeout=10)
    check('MinIO rejects expired signed URL', expired_upload.status_code == 403 and 'expired' in expired_upload.text.lower(),
          f'HTTP {expired_upload.status_code}; signed 10 minutes ago, validity 300s')
    time.sleep(.3)
    def burst(_):
        return requests.get(BASE + '/users', headers=headers, timeout=15).status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        statuses = list(pool.map(burst, range(30)))
    check('Rate limiting -> 429', 429 in statuses and 200 in statuses and all(s in (200, 429) for s in statuses),
          {str(s): statuses.count(s) for s in sorted(set(statuses))})
    (OUT / 'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')

    # Warm up both paths. Alternate order to reduce warm-up/order bias.
    time.sleep(1)
    direct = 'http://user:8000/users'
    for _ in range(3):
        requests.get(direct, headers={'X-User-ID': '1'}, timeout=10).raise_for_status()
        call('/users', headers=headers).raise_for_status()
    samples = []
    for i in range(40):
        paths = [('direct', direct, {'X-User-ID': '1'}), ('gateway', BASE + '/users', headers)]
        if i % 2:
            paths.reverse()
        for label, target, h in paths:
            time.sleep(.15)
            start = time.perf_counter()
            response = session.get(target, headers=h, timeout=15)
            elapsed = (time.perf_counter() - start) * 1000
            response.raise_for_status()
            samples.append({'path': label, 'iteration': i + 1, 'latency_ms': round(elapsed, 3), 'status': response.status_code})
    with (OUT / 'latency.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(samples[0]))
        writer.writeheader()
        writer.writerows(samples)
    summary = {}
    for label in ('direct', 'gateway'):
        values = sorted(s['latency_ms'] for s in samples if s['path'] == label)
        summary[label] = {'samples': len(values), 'mean_ms': round(statistics.mean(values), 3),
                          'median_ms': round(statistics.median(values), 3), 'p95_ms': values[37]}
    summary['mean_overhead_ms'] = round(summary['gateway']['mean_ms'] - summary['direct']['mean_ms'], 3)
    summary['method'] = '40 sequential samples/path, 3 warmups, alternating order, 150ms pauses excluded; same container client; gateway includes JWT verification'
    (OUT / 'latency-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    if not all(c['pass'] for c in checks):
        raise SystemExit('Some checks failed; see results/checks.json')


if __name__ == '__main__':
    main()
