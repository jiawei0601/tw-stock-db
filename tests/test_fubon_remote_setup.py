from types import SimpleNamespace


def test_ssh_reads_utf8_and_credentials_only_use_stdin(monkeypatch, tmp_path):
    import fubon_remote_setup as setup
    cert = tmp_path / 'certificate.p12'
    cert.write_bytes(b'fake')
    monkeypatch.setattr(setup, 'verify_pfx', lambda *args: {})
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs.get('encoding') == 'utf-8'
        assert 'test-secret' not in str(command)
        return SimpleNamespace(returncode=0, stdout='credentials_installed' if len(calls) == 1 else '【富邦券商庫存查詢成功】\n今日庫存')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.setup('user', 'test-secret', str(cert), 'cert-secret')
    assert result['status'] == 'remote_validation_success'
    assert 'test-secret' in calls[0][1]['input']
    assert 'input' not in calls[1][1]
