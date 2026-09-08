import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import pytest
import fubon_login_check as check


def test_windows_native_reader_with_generated_password_protected_pfx(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization.pkcs12 import serialize_key_and_certificates
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'Local test certificate')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256()))
    path=tmp_path/'test.pfx'
    path.write_bytes(serialize_key_and_certificates(b'test',key,cert,None,serialization.BestAvailableEncryption(b'test-only-secret')))
    r=check.subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(check.ROOT/'deploy'/'check_fubon_pfx.ps1')],input=json.dumps(dict(path=str(path),password='test-only-secret')),capture_output=True,text=True,timeout=30)
    result=json.loads(r.stdout)
    assert result['ok'] and result['has_private_key']
    assert 'test-only-secret' not in r.stdout+r.stderr


def test_native_fallback_receives_password_only_via_stdin(tmp_path,monkeypatch):
    path=tmp_path/'certificate.pfx';path.write_bytes(b'format-requiring-native-reader')
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(ok=True,has_private_key=True,not_before='2026-01-01T00:00:00Z',not_after='2027-01-01T00:00:00Z')))
    monkeypatch.setattr(check.subprocess,'run',run)
    result=check.verify_pfx(path,'test-only-secret')
    assert result['reader']=='windows'
    assert 'test-only-secret' not in str(calls[0][0])
    assert json.loads(calls[0][1]['input'])['password']=='test-only-secret'
    assert 'test-only-secret' not in json.dumps(result)


def test_both_readers_fail_without_exposing_secret(tmp_path,monkeypatch):
    path=tmp_path/'bad.pfx';path.write_bytes(b'bad')
    monkeypatch.setattr(check.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(dict(ok=False,hresult='80070056'))))
    with pytest.raises(ValueError,match='80070056') as error:check.verify_pfx(path,'test-only-secret')
    assert 'test-only-secret' not in str(error.value)
