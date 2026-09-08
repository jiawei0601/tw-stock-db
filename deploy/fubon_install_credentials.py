"""Hetzner SSH stdin接收設定；不印秘密、不接受任意寫入路徑。"""
import base64
import json
import os
import sys
from pathlib import Path


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent / 'private'
    payload = json.load(sys.stdin)
    if set(payload) != {'personal_id', 'api_key', 'cert_password', 'certificate_base64'}:
        raise ValueError('invalid_fields')
    if any(not isinstance(payload[k], str) for k in payload):
        raise ValueError('invalid_type')
    if not payload['personal_id'] or not payload['api_key']:
        raise ValueError('missing_credentials')
    certificate = base64.b64decode(payload.pop('certificate_base64'), validate=True)
    if not 100 <= len(certificate) <= 1024 * 1024:
        raise ValueError('certificate_size')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    for name, value in [('certificate.p12', certificate),
                        ('credentials.json', json.dumps(payload).encode('utf-8'))]:
        tmp = root / (name + '.tmp')
        with open(tmp, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(value)
        tmp.replace(root / name)
    print('credentials_installed')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('credential_install_failed')
        sys.exit(1)
