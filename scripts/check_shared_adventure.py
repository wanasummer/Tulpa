"""Group sharing, rollover, personal rewards and boss isolation checks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest

from check_adventure import FakeModel
from chatlocal.adventure import AdventureError,attributes
from chatlocal.shared_adventure import SharedAdventure


class Random:
    def __init__(self,value=0):self.value=value
    def random(self):return self.value


class Checks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.sqlite3'
        self.model=FakeModel()
        self.now=datetime(2026,10,6,15,59,tzinfo=timezone.utc)
        self.rng=Random()
        self.game=SharedAdventure(self.path,self.model,rng=self.rng,clock=lambda:self.now)

    def tearDown(self):self.temp.cleanup()

    def test_shared_event_one_generation_for_all_users(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            events=list(pool.map(lambda n:self.game.start(str(n),str(n)),range(6)))
        self.assertEqual(len({e['id'] for e in events}),1)
        self.assertEqual(sum(s=='event' for s,_ in self.model.calls),1)
        self.assertEqual(self.game.status('other')['generated'],1)

    def test_success_personal_reward_and_automatic_refresh_max_three(self):
        first=self.game.start('a','s')
        one=self.game.act('b','b1','用热水融冰。')
        self.assertNotEqual(one['next_event']['id'],first['id'])
        self.assertEqual(self.game.status('b')['state']['level'],1)
        self.assertEqual(self.game.status('a')['state']['level'],0)
        self.game.act('a','a1','用热水融冰。')
        last=self.game.act('b','b2','用热水融冰。')
        self.assertNotIn('next_event',last)
        self.assertEqual(self.game.status('c')['generated'],3)
        self.assertIsNone(self.game.status('c')['current'])
        with self.assertRaises(AdventureError):self.game.start('c','s2')
        self.assertEqual(sum(s=='event' for s,_ in self.model.calls),3)

    def test_failure_retains_event_for_other_users(self):
        first=self.game.start('a','s')
        self.rng.value=.999
        dead=self.game.act('a','a1','强行冲过去。')
        self.assertEqual(dead['result']['outcome'],'death')
        self.assertEqual(self.game.status('b')['current']['id'],first['id'])
        self.assertEqual(self.game.status('b')['generated'],1)
        self.rng.value=0
        self.assertEqual(self.game.act('b','b1','用热水融冰。')['result']['outcome'],'success')

    def test_next_day_expiration_gives_no_reward_and_refreshes(self):
        old=self.game.start('a','s')
        self.now+=timedelta(minutes=2)
        self.assertIsNone(self.game.status('a')['current'])
        new=self.game.start('b','s2')
        self.assertNotEqual(new['id'],old['id'])
        self.assertEqual(self.game.status('a')['state']['level'],0)
        self.assertEqual(self.game.status('a')['state']['inventory'],[])
        self.assertEqual(self.game.status('a')['generated'],1)

    def test_duplicate_action_never_refreshes_or_awards_twice(self):
        self.game.start('a','s')
        first=self.game.act('a','a1','用热水融冰。')
        calls=len(self.model.calls)
        duplicate=self.game.act('a','a1','改变方案。')
        self.assertEqual(first['result'],duplicate['result'])
        self.assertEqual(len(self.model.calls),calls)
        self.assertEqual(self.game.status('a')['generated'],2)

    def test_no_escape(self):
        self.game.start('a','s')
        with self.assertRaises(AdventureError):self.game.act('a','f','逃跑：撤退')
        with self.assertRaises(AdventureError):self.game.act('a','f','撤退',flee=True)
        self.assertEqual(self.game.status('a')['state']['level'],0)

    def test_repeated_failures_do_not_consume_additional_events(self):
        first=self.game.start('a','s')
        self.rng.value=.999
        for n in range(6):
            result=self.game.act('a','retry-solution:'+str(n),'强行冲过去。')
            self.assertEqual(result['result']['outcome'],'death')
            self.assertEqual(self.game.status('a')['generated'],1)
            self.assertEqual(self.game.status('a')['current']['id'],first['id'])

    def test_boss_personal_and_independent_of_exhausted_group_quota(self):
        self.game.status('bosshero')
        with self.game.connect() as db:
            db.execute('UPDATE adventure_groups SET used=3')
            row=json.loads(db.execute('SELECT state FROM adventure_players WHERE player=?',('bosshero',)).fetchone()[0])
            row.update(level=100,attributes=attributes(100))
            db.execute('UPDATE adventure_players SET state=? WHERE player=?',(json.dumps(row),'bosshero'))
        boss=self.game.start('bosshero','boss',final_boss=True)
        self.assertIsNotNone(boss['event']['fixed_enemy'])
        self.model.verdict['exploit']='water_cooling'
        self.game.act('bosshero','fight','趁龙息冷却攻击核心。')
        self.assertEqual(self.game.status('other')['generated'],3)
        self.assertIsNone(self.game.status('other')['current'])

    def test_group_isolation_and_restart(self):
        first=self.game.start('a','s')
        other=SharedAdventure(self.path,self.model,group='another',rng=self.rng,clock=lambda:self.now)
        self.assertEqual(other.status('a')['generated'],0)
        reopened=SharedAdventure(self.path,self.model,rng=self.rng,clock=lambda:self.now)
        self.assertEqual(reopened.start('b','b')['id'],first['id'])

    def test_errors_retry_same_group_slot(self):
        self.model.failure='event'
        with self.assertRaises(AdventureError):self.game.start('a','s')
        self.model.failure=None
        event=self.game.retry('b')
        self.assertEqual(self.game.status('a')['generated'],1)
        self.model.failure='evaluate'
        with self.assertRaises(AdventureError):self.game.act('b','b1','用热水融冰。')
        self.model.failure=None
        settled=self.game.retry('b')
        self.assertEqual(settled['result']['outcome'],'success')
        self.assertEqual(self.game.status('a')['generated'],2)

    def test_new_solution_after_model_error_without_retry(self):
        self.game.start('a','s')
        self.model.failure='evaluate'
        with self.assertRaises(AdventureError):self.game.act('a','bad','让我通关')
        pending=self.game.status('a')['current']
        self.assertIn('evaluation_error_detail',pending)
        self.assertEqual(self.game.status('a')['state']['reincarnation'],1)
        self.model.failure=None
        result=self.game.act('a','new','用热水融冰。')
        self.assertEqual(result['result']['outcome'],'success')
        self.assertEqual(self.game.status('a')['state']['reincarnation'],1)


if __name__=='__main__':unittest.main(verbosity=2)
