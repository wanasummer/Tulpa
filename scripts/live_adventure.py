"""Three consecutive real DeepSeek rounds, persisted in an isolated test database."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.adventure import Adventure, display, text
from chatlocal.adventure_model import DeepSeekAdventure
from chatlocal.config import settings
from chatlocal.support_budget import SupportBudget


def run():
    folder=ROOT/'.tmp'/'adventure-sandbox'
    folder.mkdir(parents=True,exist_ok=True)
    session=uuid.uuid4().hex[:12]
    player='sandbox:live-demo:'+session
    budget=SupportBudget(folder/'test-costs.sqlite3')
    deepseek=DeepSeekAdventure(budget,settings)
    report={'session':session,'player':player,'model':settings()['MODEL'],
            'started_at':datetime.now(timezone.utc).isoformat(),
            'synthetic_player_actions':True,'calls':[],'rounds':[]}
    report_path=folder/('real-'+session+'.json')
    def save():
        temporary=report_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(report_path)
    def tracked(stage,context):
        result=deepseek(stage,context)
        report['calls'].append({'stage':stage,'response':result})
        save()
        return result
    game=Adventure(folder/'live.sqlite3',tracked)
    print('真实 DeepSeek 连续三轮试玩（行动由模型模拟玩家，不发送群消息）',flush=True)
    try:
        for index,mode in enumerate(('normal','absurd','flee'),1):
            print(f'\n第{index}轮 · {mode}',flush=True)
            event=game.start(player,f'{session}:start:{index}')
            print(display(event),flush=True)
            action=text(tracked('test_action',dict(mode=mode,player=event['before'],event=event['event'])).get('action'),1500)
            print('模拟玩家：'+action,flush=True)
            settled=game.act(player,f'{session}:act:{index}',action,flee=mode=='flee')
            print(display(settled),flush=True)
            status=game.status(player)
            result=settled['result']
            expected_gain=0 if result['outcome']!='success' else {'weak':1,'normal':1,'hard':2,'extreme':3}[event['event']['difficulty']]
            if result['gain']!=expected_gain or status['remaining']!=3-index:
                raise AssertionError('真实试玩结算与次数不一致。')
            if result['outcome']=='death' and (status['state']['level']!=0 or status['state']['inventory']):
                raise AssertionError('失败后未完整清零。')
            # Reopen the same database, then replay the message: no fresh cloud call or reward.
            reopened=Adventure(folder/'live.sqlite3',tracked)
            call_count=len(report['calls'])
            duplicate=reopened.act(player,f'{session}:act:{index}',action,flee=mode=='flee')
            if duplicate['result']!=settled['result'] or len(report['calls'])!=call_count:
                raise AssertionError('重启后的消息重放重复调用或发奖。')
            report['rounds'].append(dict(mode=mode,event=event['event'],action=action,
                                         evaluation=settled['evaluation'],result=result,state=status['state'],
                                         remaining=status['remaining'],restart_idempotency_passed=True))
            save()
            print('今日剩余：'+str(status['remaining']),flush=True)
        report['passed']=True
    except Exception as exc:
        report['passed']=False
        report['error']=str(exc)
        raise
    finally:
        report['budget']=budget.status()
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        save()
        print('\n报告：'+str(report_path),flush=True)
        print('测试账本累计费用：'+str(report['budget']['spent_yuan'])+'元',flush=True)
    return report_path


if __name__=='__main__':
    run()
