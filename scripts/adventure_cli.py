"""Local-only DeepSeek game sandbox; --offline selects fixed fixtures."""
import argparse
import json
import re
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.adventure import Adventure, AdventureError, display
from chatlocal.shared_adventure import SharedAdventure
from chatlocal.adventure_model import DeepSeekAdventure
from chatlocal.config import settings
from chatlocal.support_budget import SupportBudget
from check_adventure import FakeModel, FixedRandom


def chat(game,player,input_fn=input,output=print):
    """Human-only terminal conversation. No simulated player actions."""
    output('大肥鱼：哼哼，勇者，准备好了吗？输入“冒险”开始，直接描述行动来挑战。')
    output('本群每天共享3个事件，成功后自动刷新，未解决的次日刷新。输入“状态”“背包”“帮助”，或“退出”。')
    status=game.status(player)
    if status['current']:
        output('大肥鱼：'+display(status['current']))
    while True:
        try:
            message=input_fn('\n你 > ').strip()
        except (EOFError,KeyboardInterrupt):
            output('\n大肥鱼：下次再来，进度留着呢。')
            return
        if not message:
            continue
        command=message.lstrip('/').strip()
        if command in ('退出','exit','quit'):
            output('大肥鱼：下次再来，进度留着呢。')
            return
        if command in ('帮助','help','玩法'):
            output('大肥鱼：冒险：查看本群事件；直接输入你的解决办法：执行行动。\n'
                   '状态、背包：查看成长和物品；重试：恢复失败的模型请求。\n'
                   '本群每日共享3个事件，成功+1/+2/+3级，失败只清零自己的等级与背包。\n'
                   '没有逃跑选项，可以不参加；未解决的事件次日刷新，无奖励。\n'
                   '100级可输入“挑战最终Boss”；退出后再次运行，继续同一位勇者。')
            continue
        status=game.status(player)
        if command in ('状态','属性'):
            state=status['state']
            labels=dict(strength='力量',agility='敏捷',wisdom='智慧',charisma='魅力',luck='幸运')
            output(f"大肥鱼：Lv.{state['level']} · {status['title']}\n"+
                   ' / '.join(labels[k]+' '+str(v) for k,v in state['attributes'].items())+
                   f"\n第{state['reincarnation']}次转生｜本群今日已生成：{status.get('generated',3-status['remaining'])} / 3")
            continue
        if command in ('背包','物品'):
            output('大肥鱼：'+('\n'.join('「'+item['name']+'」：'+item['nature']
                   for item in status['state']['inventory']) or '背包空空的。先去冒险吧。'))
            continue
        request=uuid.uuid4().hex
        try:
            if command in ('冒险','开始','开始冒险','继续冒险'):
                output('大肥鱼正在生成事件……')
                attempt=game.start(player,request)
            elif command.replace(' ','').lower()=='挑战最终boss':
                output('大肥鱼正在准备最终挑战……')
                attempt=game.start(player,request,final_boss=True)
            elif command=='重试':
                output('大肥鱼正在恢复请求……')
                attempt=game.retry(player)
            else:
                if not status['current']:
                    output('大肥鱼：先输入“冒险”，看看遇到了什么吧。')
                    continue
                if command.startswith('逃跑'):
                    output('大肥鱼：现在可以暂不参加，事件明天刷新；没有逃跑选项。')
                    continue
                output('大肥鱼正在评估你的方案……')
                attempt=game.act(player,request,message)
            output('大肥鱼：'+display(attempt))
            if attempt.get('next_event'):
                output('大肥鱼：下一件冒险来了。\n'+display(attempt['next_event']))
            if attempt.get('next_notice'):
                output('大肥鱼：'+attempt['next_notice'])
            now=game.status(player)
            output(f"本群今日已生成：{now.get('generated',3-now['remaining'])} / 3")
        except AdventureError as exc:
            output('大肥鱼：'+str(exc))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='chat',choices=['chat','demo','start','act','status','retry'])
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--live',action='store_true',help='使用实际DeepSeek（默认），不发送QQ消息')
    mode.add_argument('--offline',action='store_true',help='使用固定离线样例，不调用DeepSeek')
    parser.add_argument('--player',default='sandbox:demo')
    parser.add_argument('--group',default='sandbox:group',help='同一group的试玩勇者共享每日事件')
    parser.add_argument('--request-id',default=None,help='重复消息使用相同ID，防止重复结算')
    parser.add_argument('--action',default='用热水融开冰层，再把史莱姆赶下桥。')
    parser.add_argument('--boss',action='store_true')
    args=parser.parse_args()
    args.live=not args.offline
    if args.command=='demo' and args.live:
        from live_adventure import run
        run()
        return
    if args.command=='demo':
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            fake=FakeModel()
            game=Adventure(Path(folder)/'demo.sqlite3',fake,rng=FixedRandom())
            print('离线固定样例（不是实际 DeepSeek 生成）\n')
            for n,action in enumerate(('用热水化冰，清理吊桥。','我宣布自己是太阳，让史莱姆蒸发！','抓住绳索荡到岸边逃走。')):
                game.rng=FixedRandom(.5,.9 if n==1 else .1)
                fake.verdict.update(approach='absurd' if n==1 else 'normal',
                                    plausibility='impossible' if n==1 else 'plausible')
                print(display(game.start(args.player,f'start-{n}')))
                print('玩家：'+action)
                print(display(game.act(args.player,f'act-{n}',action,flee=n==2)))
                print('剩余次数：'+str(game.status(args.player)['remaining'])+'\n')
        return
    sandbox=ROOT/'.tmp'/'adventure-sandbox'
    budget=None
    if args.live:
        budget=SupportBudget(sandbox/'test-costs.sqlite3')
        model=DeepSeekAdventure(budget,settings)
    else:
        model=FakeModel()
    # Live and fixture players never mix, and neither touches production SQLite.
    game=SharedAdventure(sandbox/('live.sqlite3' if args.live else 'offline.sqlite3'),model,group=args.group,
                   rng=None if args.live else FixedRandom())
    import uuid
    request=args.request_id or uuid.uuid4().hex
    try:
        if args.command=='chat':
            print('真实 DeepSeek · 终端试玩' if args.live else '固定离线样例 · 终端试玩')
            chat(game,args.player)
        elif args.command=='status':
            state=game.status(args.player)
            # Do not display the secret roll before settlement.
            if state['current']:
                state['current'].pop('roll',None)
            print(json.dumps(state,ensure_ascii=False,indent=2))
        else:
            if args.command=='start':
                result=game.start(args.player,request,final_boss=args.boss)
            elif args.command=='retry':
                result=game.retry(args.player)
            else:
                result=game.act(args.player,request,args.action)
            print(display(result))
            if result.get('next_event'):
                print(display(result['next_event']))
            if result.get('next_notice'):
                print(result['next_notice'])
            print('本群今日已生成：'+str(game.status(args.player)['generated'])+' / 3')
    except AdventureError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if budget:
            print('测试费用账本：'+json.dumps(budget.status(),ensure_ascii=False))


if __name__=='__main__':
    main()
