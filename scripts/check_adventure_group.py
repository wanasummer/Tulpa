"""Production receiver integration checks; fake QQ and mock DeepSeek only."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from check_adventure import FakeModel
from chatlocal.adventure_group import command
from chatlocal.support_bot import SupportBot
from chatlocal.support_budget import DAILY_LIMIT,BudgetExceeded
from chatlocal.adventure_model import DeepSeekAdventure


def message(mid,text,uid='3',targets=('1',)):
    return dict(id=int(mid),conversation_id='1:group:2',onebot_message_id=mid,sender='群友',sender_id=uid,
                content='@1 '+text,segments=[dict(type='at',qq=t) for t in targets]+[dict(type='text',text=text)])


class Checks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.calls=[]
        self.fake=FakeModel()
        def provider(request):
            payload=json.loads(request.content)
            self.calls.append(payload)
            system=payload['messages'][0]['content']
            context=json.loads(payload['messages'][1]['content'])
            if '候选' in system or '安全审核员' in system:
                output=dict(allow=True,risks={k:False for k in ('code','internal_business','secret','injection','unsupported')},reason='safe')
            else:
                stage='event' if '生成一次可自由发挥' in system else 'evaluate' if '评估行动的因果合理性' in system else 'narrate'
                output=self.fake(stage,context)
            return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(output,ensure_ascii=False)))],usage=dict(prompt_tokens=200,completion_tokens=100)))
        chat=Mock()
        service=SimpleNamespace(access=SimpleNamespace(store=SimpleNamespace(path=Path(self.temp.name)/'chats.sqlite3')),
                                tools=SimpleNamespace(chat=chat))
        self.bot=SupportBot(service,project_root=Path(self.temp.name)/'project',
            config_factory=lambda:dict(API_BASE='https://api.deepseek.com',API_KEY='test-key',MODEL='deepseek-flash'),
            transport=httpx.MockTransport(provider))
        self.bot.group_owner=lambda cid:'3'

    def tearDown(self):self.temp.cleanup()

    def test_explicit_mentions_owner_control_and_disabled_default(self):
        bot=self.bot
        self.assertIsNone(command(message('1','冒险',targets=())))
        self.assertIsNone(command(message('1','冒险',targets=('1','9'))))
        self.assertIsNone(command(message('1','我想去冒险')))
        self.assertEqual(bot.classify(message('1','行动 我的法术失败后怎么办')),'adventure')
        self.assertIn('尚未开启',bot.adventure.answer(message('1','冒险')))
        self.assertIn('需要群主',bot.adventure.answer(message('2','游戏开启',uid='4')))
        self.assertIn('已开启',bot.adventure.answer(message('3','游戏开启')))
        self.assertEqual(self.calls,[])

    def test_group_generation_uses_shared_five_yuan_ledger(self):
        bot=self.bot
        bot.adventure.answer(message('1','游戏开启'))
        first=bot.adventure.answer(message('2','冒险'))
        second=bot.adventure.answer(message('3','冒险',uid='4'))
        self.assertEqual(first,second)
        self.assertEqual(len(self.calls),1)
        with bot.budget.connect() as db:
            costs=db.execute('SELECT kind,cost FROM support_costs').fetchall()
        self.assertEqual(costs[0]['kind'],'adventure')
        self.assertGreater(costs[0]['cost'],0)
        self.assertGreater(bot.budget.status()['spent_yuan'],0)
        with bot.budget.connect() as db:
            db.execute('INSERT INTO support_costs(id,day,kind,model,price,reserve,cost,state) VALUES(?,?,?,?,?,?,?,?)',
                       ('cap',bot.budget.day(),'technical','deepseek-flash','test',DAILY_LIMIT,DAILY_LIMIT,'SETTLED'))
        model=DeepSeekAdventure(bot.budget,bot.config_factory,bot.transport,kind='adventure')
        with self.assertRaises(BudgetExceeded):model('event',{})
        self.assertEqual(len(self.calls),1)

    def test_receiver_game_flow_no_bug_no_casual_quota_and_review(self):
        bot=self.bot
        bot.access.by_id=lambda *args,**kwargs:dict(id='grant')
        with bot.budget.connect() as db:
            db.execute('INSERT INTO support_runs VALUES(1,?,?,?,?,1)',('grant','session','1:group:2','群'))
        events=[message('1','游戏开启'),message('2','冒险'),message('3','行动 用热水融冰，避免挑战失败。')]
        bot.chat.wait.side_effect=[dict(event='messages',messages=events,read_through_id=3,receiver=dict(state='connected')),dict(event='cancelled')]
        bot.chat.send.return_value=dict(state='SUCCEEDED',id='send')
        bot.loop()
        self.assertEqual(bot.chat.send.call_count,3)
        bot.chat.context.assert_not_called()
        with bot.budget.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM support_bugs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM support_casual_quota').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM support_reviews').fetchone()[0],3)
            self.assertEqual({r[0] for r in db.execute('SELECT kind FROM support_costs')},{'adventure'})
            self.assertEqual({r[0] for r in db.execute('SELECT state FROM support_replies')},{'SUCCEEDED'})


if __name__=='__main__':unittest.main(verbosity=2)
