#!/bin/bash
set -e

SECRET_NAME="${SECRET_NAME:-agw-root-ca-cert-dev}"

echo "🔒 Platform Trust Manager: Verifying Agent Gateway Root CA..."

# 1. First check if Root CA is already installed at build time (official BYOC path)
python3 -c "
import glob, os, certifi
certifi_path = certifi.where()
certifi_data = open(certifi_path).read() if os.path.exists(certifi_path) else ''
installed = False

for crt in glob.glob('/usr/local/share/ca-certificates/agw*.crt') + ['/app/certs/agw-gateway.crt']:
    if os.path.exists(crt):
        cert_data = open(crt).read()
        if cert_data not in certifi_data:
            with open(certifi_path, 'a') as cf:
                cf.write('\n# Agent Gateway Root CA\n' + cert_data + '\n')
            print(f'✅ Injected {crt} into certifi ({certifi_path})')
        installed = True

if installed:
    print('✅ Agent Gateway Root CA verified in trust store.')
    exit(0)
exit(1)
" && HAS_BUILTIN_CERT=1 || HAS_BUILTIN_CERT=0

# 2. If not pre-installed, fallback to Secret Manager
if [ "$HAS_BUILTIN_CERT" -eq 0 ] && [ -n "$SECRET_NAME" ]; then
    echo "🔒 Platform Trust Manager: Resolving Root CA bundle from Secret Manager ($SECRET_NAME)..."
    python3 -c "
import os, json, base64, ssl, urllib.request

try:
    secret_name = os.environ.get('SECRET_NAME', 'agw-root-ca-cert-dev')
    
    no_proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(no_proxy_handler)
    
    meta_req = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token')
    meta_req.add_header('Metadata-Flavor', 'Google')
    with opener.open(meta_req) as resp:
        token = json.loads(resp.read().decode('utf-8'))['access_token']

    if secret_name.startswith('projects/'):
        url = f'https://secretmanager.googleapis.com/v1/{secret_name}/versions/latest:access'
    else:
        url = f'https://secretmanager.googleapis.com/v1/projects/esm-dev-governance-00b1/secrets/{secret_name}/versions/latest:access'

    sec_req = urllib.request.Request(url)
    sec_req.add_header('Authorization', f'Bearer {token}')
    with opener.open(sec_req) as sec_resp:
        payload = json.loads(sec_resp.read().decode('utf-8'))
        cert_data = base64.b64decode(payload['payload']['data']).decode('utf-8')

    cert_file = '/usr/local/share/ca-certificates/agw-gateway.crt'
    with open(cert_file, 'w') as f:
        f.write(cert_data)
    os.chmod(cert_file, 0o644)
    os.system('update-ca-certificates')

    import certifi
    certifi_path = certifi.where()
    with open(certifi_path, 'a') as cf:
        cf.write('\n# Agent Gateway Root CA\n' + cert_data + '\n')
    print(f'✅ Injected Agent Gateway Root CA into certifi ({certifi_path})')
    print('✅ Successfully injected Agent Gateway Root CA bundle into system trust store.')
except Exception as e:
    print(f'⚠️ Warning: Could not fetch CA cert from Secret Manager: {e}')
"
fi

exec "$@"
