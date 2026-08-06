#!/bin/bash
set -e

if [ -n "$SECRET_NAME" ]; then
    echo "🔒 Platform Trust Manager: Resolving Root CA bundle from Secret Manager ($SECRET_NAME)..."
    python3 -c "
import os, json, base64, urllib.request

try:
    secret_name = os.environ['SECRET_NAME']
    meta_req = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token')
    meta_req.add_header('Metadata-Flavor', 'Google')
    with urllib.request.urlopen(meta_req) as resp:
        token = json.loads(resp.read().decode('utf-8'))['access_token']

    url = f'https://secretmanager.googleapis.com/v1/{secret_name}/versions/latest:access'
    sec_req = urllib.request.Request(url)
    sec_req.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(sec_req) as sec_resp:
        payload = json.loads(sec_resp.read().decode('utf-8'))
        cert_data = base64.b64decode(payload['payload']['data']).decode('utf-8')

    cert_file = '/usr/local/share/ca-certificates/agw-gateway.crt'
    with open(cert_file, 'w') as f:
        f.write(cert_data)
    os.chmod(cert_file, 0o644)
    os.system('update-ca-certificates')
    print('✅ Successfully injected Agent Gateway Root CA bundle into system trust store.')
except Exception as e:
    print(f'⚠️ Warning: Could not fetch CA cert from Secret Manager: {e}')
"
else
    echo "ℹ️ Platform Trust Manager: SECRET_NAME not set. Using standard system root CAs."
fi

exec "$@"
