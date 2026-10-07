"""Offline acceptance tests. Temporary SQLite only; no real QQ or cloud calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from chatlocal.adventure import (Adventure, AdventureError, FINAL_BOSS, attributes,
                                 probability, title,validate_evaluation)
from chatlocal.adventure_model import DeepSeekAdventure
from chatlocal.support_budget import SupportBudget


class FixedRandom:
    def __init__(self, difficulty=.5, roll=.1):
        self.values = (difficulty,roll)
        self.index = 0

    def random(self):
        value = self.values[self.index % 2]
        self.index += 1
        return value


class FakeModel:
    """Structured fixture, not a claim of actual DeepSeek story quality."""
    def __init__(self):
        self.calls = []
        self.failure = None
        self.verdict = dict(approach='normal',plausibility='plausible',attribute='wisdom',
                            item_id=None,item_help=0,exploit=None,reason='借助环境完成合理行动。',
                            reward=dict(name='水之剑',nature='剑身凝聚流动的清水，可引导水流。'))

    def __call__(self, stage, context):
        self.calls.append((stage,copy.deepcopy(context)))
        if self.failure == stage:
            raise ValueError('fixture error')
        if stage == 'event':
            return dict(title='吊桥上的寒冰史莱姆',enemy='寒冰史莱姆',
                        scene='寒冰史莱姆堵住吊桥，桥边有绳索和一桶热水。它比你慢，但会冻结桥面。你打算怎么办？',
                        traits=['会冻结桥面'],clues=['一桶热水','可绕行的绳索'])
        if stage == 'evaluate':
            return copy.deepcopy(self.verdict)
        result = context['result']
        return dict(story={'success':'热水融开冰层，你把史莱姆赶出了吊桥。哼哼，过关啦。',
                           'escaped':'你抓住绳索荡到岸边。诶？鞋还在桥上……先走吧。',
                           'death':'你宣布自己是太阳，随后被冻成了桥头的路标。哼哼，下次先穿厚点。'}[result['outcome']])


class Checks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'adventure.sqlite3'
        self.model = FakeModel()
        self.now = datetime(2026,10,6,15,59,tzinfo=timezone.utc)
        self.game = Adventure(self.path,self.model,clock=lambda:self.now,rng=FixedRandom())

    def tearDown(self):
        self.temp.cleanup()

    def level(self, player, level):
        self.game.status(player)
        with self.game.connect() as db:
            state = json.loads(db.execute('SELECT state FROM adventure_players WHERE player=?',(player,)).fetchone()[0])
            state.update(level=level,attributes=attributes(level))
            db.execute('UPDATE adventure_players SET state=? WHERE player=?',(json.dumps(state),player))

    def play(self, player='qq:test:alice', request='1', flee=False):
        self.game.start(player,'start:'+request)
        return self.game.act(player,'action:'+request,'用热水化开冰层，再把它推到桥外。',flee=flee)

    def test_success_state_restart_and_user_isolation(self):
        result = self.play()
        self.assertEqual(result['result']['gain'],1)
        reopened = Adventure(self.path,self.model)
        state = reopened.status('qq:test:alice')['state']
        self.assertEqual(state['level'],1)
        self.assertEqual(state['attributes'],attributes(1))
        self.assertEqual(state['inventory'][0]['name'],'水之剑')
        self.assertNotIn('attack',state['inventory'][0])
        self.assertEqual(reopened.status('qq:test:bob')['state']['level'],0)

    def test_rewards_for_all_difficulties(self):
        for draw,gain in ((.05,1),(.5,1),(.8,2),(.99,3)):
            self.game.rng = FixedRandom(draw,0)
            result = self.play(player=str(draw))
            self.assertEqual(result['result']['gain'],gain)

    def test_three_attempts_and_beijing_midnight(self):
        for n in range(3):
            self.play(request=str(n))
        with self.assertRaises(AdventureError):
            self.game.start('qq:test:alice','fourth')
        self.now += timedelta(minutes=2)
        self.assertEqual(self.game.status('qq:test:alice')['remaining'],3)
        self.game.start('qq:test:alice','newday')
        self.assertEqual(self.game.status('qq:test:alice')['remaining'],2)

    def test_failure_clears_level_items_and_keeps_daily_usage(self):
        self.play()
        self.level('qq:test:alice',70)
        self.game.rng = FixedRandom(.5,.999)
        result = self.play(request='death')
        self.assertEqual(result['result']['outcome'],'death')
        status = self.game.status('qq:test:alice')
        self.assertEqual(status['state']['level'],0)
        self.assertEqual(status['state']['inventory'],[])
        self.assertEqual(status['state']['attributes'],attributes(0))
        self.assertEqual(status['state']['reincarnation'],2)
        self.assertEqual(status['title'],'初来乍到')
        self.assertEqual(status['remaining'],1)

    def test_flee_success_and_failure(self):
        self.level('runner',30)
        escaped = self.play('runner',flee=True)
        self.assertEqual((escaped['result']['outcome'],escaped['result']['gain']),('escaped',0))
        self.assertEqual(self.game.status('runner')['state']['level'],30)
        self.game.rng = FixedRandom(.5,.999)
        failed = self.play('runner','2',flee=True)
        self.assertEqual(failed['result']['level'],0)

    def test_duplicate_messages_do_not_call_model_or_award_twice(self):
        result = self.play()
        calls = len(self.model.calls)
        duplicate = self.game.act('qq:test:alice','action:1','改为无敌！')
        self.assertEqual(duplicate['result'],result['result'])
        self.game.start('qq:test:alice','start:1')
        self.assertEqual(len(self.model.calls),calls)
        self.assertEqual(self.game.status('qq:test:alice')['remaining'],2)

    def test_unfinished_event_survives_day_change_and_restart(self):
        event = self.game.start('a','first')
        self.now += timedelta(days=1)
        reopened = Adventure(self.path,self.model,clock=lambda:self.now)
        self.assertEqual(reopened.start('a','second')['id'],event['id'])
        self.assertEqual(reopened.status('a')['remaining'],3)
        reopened.act('a','solve','绕过冻结的桥面。')
        self.assertEqual(reopened.status('a')['remaining'],3)

    def test_concurrent_starts_have_one_event_one_slot_one_model_call(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n:self.game.start('a',str(n)),range(8)))
        self.assertEqual(len({r['id'] for r in results}),1)
        self.assertEqual(sum(stage=='event' for stage,_ in self.model.calls),1)
        self.assertEqual(self.game.status('a')['remaining'],2)

    def test_concurrent_actions_cannot_double_settle(self):
        self.game.start('a','begin')
        entered,release = threading.Event(),threading.Event()
        model = self.model
        def blocked(stage,context):
            if stage=='evaluate':
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('test timeout')
            return model(stage,context)
        self.game.model = blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.game.act,'a','solve1','用热水融冰。')
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(AdventureError):
                    self.game.act('a','solve2','绕行。')
            finally:
                release.set()
            future.result()
        self.assertEqual(self.game.status('a')['state']['level'],1)
        self.assertEqual(len(self.game.status('a')['state']['inventory']),1)

    def test_generation_error_explicit_retry_keeps_event_roll_and_slot(self):
        self.model.failure='event'
        with self.assertRaises(AdventureError):
            self.game.start('a','start')
        before = self.game.status('a')['current']
        self.model.failure=None
        result = self.game.retry('a')
        self.assertEqual((result['id'],result['roll']),(before['id'],before['roll']))
        self.assertEqual(self.game.status('a')['remaining'],2)

    def test_evaluation_error_does_not_clear_or_reroll_player(self):
        self.level('a',20)
        event = self.game.start('a','start')
        self.model.failure='evaluate'
        with self.assertRaises(AdventureError):
            self.game.act('a','solve','使用水桶。')
        self.assertEqual(self.game.status('a')['state']['level'],20)
        self.model.failure=None
        result = self.game.retry('a')
        self.assertEqual(result['roll'],event['roll'])
        self.assertEqual(result['action'],'使用水桶。')

    def test_narration_error_keeps_already_committed_result(self):
        self.model.failure='narrate'
        result = self.play()
        self.assertTrue(result['result']['narration_fallback'])
        self.assertEqual(self.game.status('qq:test:alice')['state']['level'],1)

    def test_narration_receives_causal_evaluation_without_existing_story(self):
        self.play()
        context=next(c for stage,c in self.model.calls if stage=='narrate')
        self.assertEqual(context['evaluation']['reason'],self.model.verdict['reason'])
        self.assertNotIn('story',context['result'])

    def test_malformed_model_and_fabricated_items_do_not_award(self):
        self.game.start('a','start')
        self.model.verdict.update(item_id='imaginary',item_help=1,level=999,success=True)
        with self.assertRaises(AdventureError):
            self.game.act('a','solve','我凭空得到神剑。')
        self.assertEqual(self.game.status('a')['state']['level'],0)

    def test_model_cannot_choose_level_gain(self):
        self.model.verdict.update(level=999,gain=1000,success=True,probability=1)
        self.game.rng = FixedRandom(.5,.999)
        result = self.play()
        self.assertEqual((result['result']['gain'],result['result']['level']),(0,0))

    def test_impossible_misplaced_in_approach_is_normalized_without_success(self):
        self.model.verdict.update(approach='impossible',plausibility='impossible')
        result=self.play()
        self.assertEqual(result['evaluation']['approach'],'absurd')
        self.assertEqual(result['result']['probability'],0)
        self.assertEqual(result['result']['outcome'],'death')
        contradictory={**self.model.verdict,'plausibility':'plausible'}
        with self.assertRaises(AdventureError):
            validate_evaluation(contradictory,self.game.status('a')['state'],None)

    def test_item_logic_and_absurdity_probabilities(self):
        state = dict(level=30,attributes=attributes(30))
        event = dict(enemy_level=30,fixed_enemy=None)
        verdict = copy.deepcopy(self.model.verdict)
        verdict['approach']='absurd'
        self.assertEqual(probability(state,event,verdict),.4)
        verdict['item_help']=1
        self.assertGreater(probability(state,event,verdict),.4)
        verdict.update(item_help=0,plausibility='strained')
        weak_chance = probability(state,event,verdict)
        state.update(level=100,attributes=attributes(100))
        self.assertGreater(probability(state,event,verdict),weak_chance)
        verdict['plausibility']='impossible'
        self.assertEqual(probability(state,event,verdict),0)

    def test_weak_monsters_and_scaling(self):
        self.level('a',70)
        self.game.rng=FixedRandom(.05,0)
        event = self.game.start('a','start')['event']
        self.assertLess(event['enemy_level'],20)
        self.assertGreater(probability(self.game.status('a')['state'],event,self.model.verdict),.95)
        self.level('b',70)
        self.game.rng=FixedRandom(.8,0)
        hard = self.game.start('b','start')['event']
        self.assertGreater(hard['enemy_level'],70)

    def test_final_boss_fixed_and_brains_required(self):
        with self.assertRaises(AdventureError):
            self.game.start('a','boss',final_boss=True)
        self.level('a',100)
        self.game.rng=FixedRandom(.5,0)
        event=self.game.start('a','boss',final_boss=True)['event']
        self.assertEqual(event['fixed_enemy'],FINAL_BOSS)
        self.assertEqual(event['enemy'],FINAL_BOSS['name'])
        state=self.game.status('a')['state']
        self.assertLessEqual(probability(state,event,self.model.verdict),.1)
        verdict={**self.model.verdict,'exploit':'water_cooling'}
        self.assertGreater(probability(state,event,verdict),.7)

    def test_titles_derived_not_stored(self):
        for level in (20,30,50,70,100):
            self.assertNotEqual(title(level),title(level-1))
        self.assertNotIn('achievements',self.game.status('a')['state'])

    def test_inflight_calls_not_automatically_replayed_after_restart(self):
        self.game.start('a','start')
        with self.game.connect() as db:
            db.execute("UPDATE adventure_attempts SET phase='evaluating'")
        calls=len(self.model.calls)
        reopened=Adventure(self.path,self.model)
        with self.assertRaises(AdventureError):
            reopened.retry('a')
        self.assertEqual(len(self.model.calls),calls)

    def test_deepseek_adapter_json_and_costs_with_mock_transport(self):
        requests=[]
        fake=FakeModel()
        def reply(request):
            payload=json.loads(request.content)
            requests.append(payload)
            stage=('event','evaluate','narrate')[len(requests)-1]
            context=json.loads(payload['messages'][1]['content'])
            return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(
                content=json.dumps(fake(stage,context),ensure_ascii=False)))],
                usage=dict(prompt_tokens=500,completion_tokens=100)))
        budget=SupportBudget(Path(self.temp.name)/'cost.sqlite3')
        model=DeepSeekAdventure(budget,lambda:dict(API_BASE='https://api.deepseek.com',
            API_KEY='fixture-key',MODEL='deepseek-flash'),httpx.MockTransport(reply))
        self.game.model=model
        result=self.play()
        self.assertEqual(result['result']['gain'],1)
        self.assertEqual(len(requests),3)
        self.assertEqual(requests[0]['response_format'],{'type':'json_object'})
        self.assertNotIn('roll',requests[1]['messages'][1]['content'])
        self.assertGreater(budget.status()['spent_yuan'],0)
        self.assertEqual(budget.status()['unresolved'],0)


if __name__=='__main__':
    unittest.main(verbosity=2)
