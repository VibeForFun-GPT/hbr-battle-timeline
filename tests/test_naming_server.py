"""验证自动姓名在 API、手动 Profile、保存和换位记录中的一致性。"""
import copy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from app import Store, catalog_status, make_handler, validate_document, validate_profile
from axis.avatar_names import AvatarNameMatcher
from axis.model import build_team
from axis.pipeline import identify_team_names, save_json
from axis.vision import save_image
from _support import write_synthetic_catalog


class NamingServerTests(unittest.TestCase):
    def test_catalog_status_rejects_malformed_cache_without_breaking_bootstrap(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for malformed in ([],None,"wrong",{"schema_version":1,"styles":[None,{},42]},
                              {"schema_version":2,"styles":[]}):
                save_json(folder/'catalog.json',malformed)
                with patch('app.CATALOG_FOLDER',folder):
                    result=catalog_status()
                    self.assertFalse(result['ready'])
                    self.assertEqual(result['styles'],0)

    def test_identify_preserves_manual_names_and_updates_ids_only_on_save(self):
        expected=[f'合成队员{index}' for index in range(1,7)]
        with tempfile.TemporaryDirectory() as temporary:
            data=Path(temporary);folder=data/'jobs'/'naming-test';folder.mkdir(parents=True)
            catalog_path,avatars=write_synthetic_catalog(data/'synthetic-catalog')
            matcher=AvatarNameMatcher.load(catalog_path)
            self.assertTrue(matcher.ready)
            team=build_team()
            for index,person in enumerate(team,1):
                person.update(id=index,avatar=f'avatars/{index}.jpg')
                save_image(folder/person['avatar'],avatars[index-1])
            team[0].update(name='用户指定的完整姓名',alias='我的队员',name_source='manual',name_is_placeholder=False)
            rows=[]
            for n,ids in enumerate(([1,2,3],[6,1,4]),1):
                actions=[dict(character_id=i,character=team[i-1]['alias'],character_full=team[i-1]['name'],slot=s,
                              skill='任意新增 Skill',skill_full='任意新增 Skill',skill_raw='任意新增 Skill') for s,i in enumerate(ids,1)]
                rows.append(dict(id=f'row-{n}',time=n,confirm_time=n+.1,turn=n,kind='normal',label=str(n),actions=actions,
                                 od_before=None,od_after=None,needs_review=True,reviewed=False,notes=['OD 不可读']))
            doc=dict(schema_version=1,title='Naming API Fixture',team=team,rows=rows,settings={'layout':{}},
                     created_at='2026-10-05T00:00:00+08:00',video={'duration':10,'path':'fixture.mp4'})
            save_json(folder/'axis.json',doc)
            save_json(folder/'axis.automatic.json',doc)
            store=Store(data)
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(store,'naming'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=30)
            try:
                with patch('axis.pipeline.AvatarNameMatcher.load',return_value=matcher):
                    conn.request('POST','/naming/api/names/naming-test','{}',{'Content-Type':'application/json'})
                    response=conn.getresponse();result=json.loads(response.read());self.assertEqual(response.status,200,result)
                self.assertEqual(result['kept_manual'],1)
                self.assertEqual(result['matched'],5)
                self.assertEqual(result['unresolved'],0)
                self.assertEqual(result['team'][0]['alias'],'我的队员')
                self.assertEqual([p['name'] for p in result['team'][1:]],expected[1:])
                self.assertEqual(store.document('naming-test'),doc)
                edited=copy.deepcopy(doc);edited['team']=result['team']
                conn.request('POST','/naming/api/save/naming-test',json.dumps({'document':edited}),{'Content-Type':'application/json'})
                response=conn.getresponse();saved=json.loads(response.read());self.assertEqual(response.status,200,saved)
                saved=saved['document']
                self.assertEqual([a['character_full'] for a in saved['rows'][1]['actions']],
                                  [expected[5],'用户指定的完整姓名',expected[3]])
                self.assertEqual(saved['rows'][1]['notes'],['OD 不可读'])
                self.assertEqual(saved['rows'][1]['actions'][0]['skill_raw'],'任意新增 Skill')
                self.assertEqual(json.loads((folder/'axis.automatic.json').read_text(encoding='utf8')),doc)
                self.assertEqual([p['id'] for p in saved['team']],list(range(1,7)))
                self.assertEqual(saved['team'][5]['name_match']['database_character_id'],'Synthetic6')
            finally:
                conn.close();store.close();server.shutdown();server.server_close();thread.join()

    def test_missing_catalog_keeps_placeholder_and_explicit_profile(self):
        from axis.avatar_names import AvatarNameMatcher
        with tempfile.TemporaryDirectory() as temporary:
            matcher=AvatarNameMatcher.load(Path(temporary)/'missing.json')
            team=build_team()
            identify_team_names(team,[None]*6,matcher)
            self.assertTrue(all(p['name_is_placeholder'] for p in team))
            self.assertTrue(all(not p['name_match']['accepted'] for p in team))
            named=build_team([{'name':f'名字 {i}','alias':f'简称 {i}'} for i in range(6)])
            original=copy.deepcopy(named)
            identify_team_names(named,[None]*6,matcher)
            self.assertEqual(named,original)
            self.assertEqual(validate_profile({'team':[{'name':p['name']} for p in named]})['name_mode'],'profile')


if __name__=='__main__':unittest.main()
