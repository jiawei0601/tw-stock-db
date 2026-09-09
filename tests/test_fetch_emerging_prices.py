import json
import pytest
import fetch_emerging_prices as fetcher
from fetch_momentum_pit import FetchStopped


@pytest.mark.parametrize('stopped',[False,True])
def test_downloader_exit_code_tracks_completion_without_network(tmp_path,monkeypatch,stopped):
    monkeypatch.chdir(tmp_path)
    root=tmp_path/'data'/'momentum_pit'
    root.mkdir(parents=True)
    (root/'manifest.json').write_text(json.dumps(dict(start='2018-12-01',end='2026-09-07',candidate_ids=['1111'])))
    monkeypatch.setattr(fetcher,'ROOT',root/'emerging_universe')
    monkeypatch.setattr(fetcher,'token_from_env',lambda:'')
    monkeypatch.setattr('sys.argv',['fetch_emerging_prices.py'])

    class Client:
        used=1
        def __init__(self,*args):pass
        def fetch(self,query):
            if query['dataset']=='TaiwanStockInfo':return [dict(stock_id='2222',type='emerging')]
            if stopped:raise FetchStopped('network_error: URLError')
            return []

    monkeypatch.setattr(fetcher,'Client',Client)
    if stopped:
        with pytest.raises(SystemExit) as exc:fetcher.main()
        assert exc.value.code==1
    else:fetcher.main()
    status=json.loads((fetcher.ROOT/'price_status.json').read_text())
    assert status['state']==('stopped' if stopped else 'complete')
    assert status['total']==1
