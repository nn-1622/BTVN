"""Two service processes; Nginx delegates JWT validation to user/auth/verify."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import jwt
import requests
from flask import Flask, jsonify, request
from minio import Minio
from minio.error import S3Error

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16384
ROLE = os.getenv('SERVICE', 'user')
SECRET = os.getenv('JWT_SECRET', 'local-lab-jwt-secret-change-before-deploy-12345')
USERS = {'1': {'id': '1', 'name': 'Nguyen Van An', 'email': 'an@example.com'},
         '2': {'id': '2', 'name': 'Tran Thi Binh', 'email': 'binh@example.com'}}
ORDERS = [{'id': 'O001', 'user_id': '1', 'product': 'Keyboard', 'total': 500000},
          {'id': 'O002', 'user_id': '1', 'product': 'Mouse', 'total': 200000},
          {'id': 'O003', 'user_id': '2', 'product': 'Monitor', 'total': 3000000}]


def identity():
    # Gateway overwrites this header using validated JWT sub, never client input.
    uid = request.headers.get('X-User-ID', '')
    return uid if uid in USERS else None


@app.get('/health')
def health():
    return jsonify(service=ROLE, status='ok')


@app.post('/auth/login')
def login():
    if ROLE != 'user':
        return jsonify(error='Not found'), 404
    body = request.get_json(silent=True) or {}
    uid = next((k for k, v in USERS.items() if v['email'] == body.get('email')), None)
    if not uid or body.get('password') != 'demo123':
        return jsonify(error='Invalid credentials'), 401
    now = int(time.time())
    token = jwt.encode({'sub': uid, 'iat': now, 'exp': now + 3600,
                        'iss': 'btvn-user', 'aud': 'btvn-api'}, SECRET, algorithm='HS256')
    return jsonify(access_token=token, token_type='Bearer', expires_in=3600)


@app.get('/auth/verify')
def verify():
    if ROLE != 'user':
        return '', 404
    try:
        scheme, token = request.headers.get('Authorization', '').split(' ', 1)
        if scheme.lower() != 'bearer':
            raise ValueError('Wrong scheme')
        claims = jwt.decode(token, SECRET, algorithms=['HS256'], issuer='btvn-user',
                            audience='btvn-api', options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
        if claims['sub'] not in USERS:
            raise ValueError('Unknown user')
    except (ValueError, jwt.InvalidTokenError):
        return '', 401, {'WWW-Authenticate': 'Bearer'}
    return '', 204, {'X-User-ID': claims['sub']}


@app.get('/users')
@app.get('/users/<uid>')
def users(uid=None):
    if ROLE != 'user':
        return jsonify(error='Not found'), 404
    current = identity()
    if not current:
        return jsonify(error='Missing trusted user identity'), 401
    if uid and uid != current:
        return jsonify(error='Cannot view another user'), 403
    return jsonify(service='user', user=USERS[current])


@app.get('/orders')
def orders():
    if ROLE != 'order':
        return jsonify(error='Not found'), 404
    current = identity()
    if not current:
        return jsonify(error='Missing trusted user identity'), 401
    return jsonify(service='order', orders=[o for o in ORDERS if o['user_id'] == current])


@app.get('/dashboard')
def dashboard():
    if ROLE != 'user':
        return jsonify(error='Not found'), 404
    current = identity()
    if not current:
        return jsonify(error='Missing trusted user identity'), 401
    # Fan out to both service APIs concurrently; no extra business service needed.
    def fetch(url):
        response = requests.get(url, headers={'X-User-ID': current}, timeout=3)
        response.raise_for_status()
        return response.json()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            u = pool.submit(fetch, 'http://user:8000/users')
            o = pool.submit(fetch, 'http://order:8000/orders')
            user_data, order_data = u.result(), o.result()
    except (requests.RequestException, ValueError):
        return jsonify(error='A dashboard dependency is unavailable'), 502
    items = order_data['orders']
    return jsonify(user=user_data['user'], orders=items,
                   summary={'order_count': len(items), 'total': sum(o['total'] for o in items)})


@app.post('/users/avatar/upload-url')
def avatar_url():
    if ROLE != 'user':
        return jsonify(error='Not found'), 404
    current = identity()
    if not current:
        return jsonify(error='Missing trusted user identity'), 401
    body = request.get_json(silent=True) or {}
    suffix = {'image/png': '.png', 'image/jpeg': '.jpg'}.get(body.get('content_type'))
    if not suffix:
        return jsonify(error='Use image/png or image/jpeg'), 400
    creds = {'access_key': os.getenv('MINIO_ACCESS_KEY', 'labadmin'),
             'secret_key': os.getenv('MINIO_SECRET_KEY', 'lab-password-12345'),
             'secure': False, 'region': 'us-east-1'}
    internal = Minio('minio:9000', **creds)
    try:
        if not internal.bucket_exists('avatars'):
            try:
                internal.make_bucket('avatars')
            except S3Error as error:
                if error.code not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
                    raise
        # Sign with public host; never rewrite host after signing a SigV4 URL.
        public = Minio(os.getenv('MINIO_PUBLIC', 'localhost:19000'), **creds)
        key = f'{current}/{uuid4().hex}{suffix}'
        url = public.presigned_put_object('avatars', key, expires=timedelta(minutes=5))
    except Exception:
        app.logger.exception('MinIO signing failed')
        return jsonify(error='Object storage unavailable'), 503
    return jsonify(method='PUT', upload_url=url, expires_in=300, bucket='avatars', object_key=key)
