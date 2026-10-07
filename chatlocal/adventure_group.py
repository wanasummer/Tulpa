"""Explicit @bot game commands for the existing support receiver."""
import re

from .adventure import AdventureError,display
from .adventure_model import DeepSeekAdventure
from .shared_adventure import SharedAdventure

HELP='''大肥鱼的异世界勇者冒险：
群主发送“@我 游戏开启”，即可开放本群游戏。
大家发送“@我 冒险”查看事件；“@我 行动 你的方案”提交解法。
“@我 游戏状态”“@我 游戏背包”查看自己的勇者；“@我 游戏重试”恢复模型请求。
每群每天共3个事件，可无限提交新方案；一人成功后刷新下一事件。
失败只清零挑战者，事件保留；没人解决的事件次日刷新，不发奖。
100级可“@我 挑战最终Boss”，不占群事件名额。群主可“@我 游戏关闭”。'''


def command(message):
    if message.get('is_self'):
        return None
    cid=message.get('conversation_id','')
    if not cid:
        return None
    account=cid.split(':')[0]
    segments=message.get('segments',[])
    targets={str(s.get('qq','')) for s in segments if s.get('type')=='at'}
    if targets!={account}:
        return None
    parts=[s.get('text','') for s in segments if s.get('type')=='text']
    content=''.join(parts).strip() if parts else re.sub(r'^\s*@'+re.escape(account)+r'\s*','',message.get('content','')).strip()
    aliases={'游戏开启':'enable','游戏关闭':'disable','游戏帮助':'help','冒险':'start',
             '游戏状态':'status','游戏背包':'bag','游戏重试':'retry','挑战最终Boss':'boss','挑战最终boss':'boss'}
    if content in aliases:
        return aliases[content],''
    match=re.match(r'^行动(?:[：:\s]+)(.+)$',content,re.S)
    if match:
        return 'act',match[1].strip()
    if content=='行动':
        return 'help',''
    return None


class GroupAdventure:
    def __init__(self,bot):
        self.bot=bot
        with bot.budget.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS adventure_enabled(cid TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')

    def answer(self,message):
        op,action=command(message)
        cid=message['conversation_id']
        uid=str(message.get('sender_id',''))
        if op=='help':
            return HELP
        if op in ('enable','disable'):
            try:
                owner=str(self.bot.group_owner(cid))
            except Exception:
                return '暂时无法确认群主身份，请稍后重试。'
            if not owner or uid!=owner:
                return '哼哼，游戏开启和关闭需要群主操作。可以先发送“游戏帮助”看看玩法。'
            with self.bot.budget.connect() as db:
                db.execute('INSERT OR REPLACE INTO adventure_enabled VALUES(?,?)',(cid,int(op=='enable')))
            return ('本群勇者冒险已开启！发送“@我 冒险”查看今天的第一个事件，大家可讨论后用“@我 行动 方案”挑战。'
                    if op=='enable' else '本群游戏已暂停，勇者进度保留。')
        with self.bot.budget.connect() as db:
            enabled=db.execute('SELECT enabled FROM adventure_enabled WHERE cid=?',(cid,)).fetchone()
        if not enabled or not enabled[0]:
            return '本群游戏尚未开启，请群主发送“@我 游戏开启”。玩法可发送“@我 游戏帮助”。'
        if not uid.isdecimal():
            return '无法识别勇者身份，请使用自己的群账号发送游戏命令。'
        game=SharedAdventure(self.bot.path,DeepSeekAdventure(self.bot.budget,self.bot.config_factory,
                             self.bot.transport,kind='adventure'),group=cid)
        player='qq:'+cid.split(':')[0]+':'+uid
        request=cid+':'+str(message['onebot_message_id'])
        try:
            if op=='status':
                status=game.status(player)
                state=status['state']
                return (f"Lv.{state['level']} · {status['title']}｜第{state['reincarnation']}次转生\n"+
                        ' / '.join(name+' '+str(state['attributes'][key]) for key,name in
                                   [('strength','力量'),('agility','敏捷'),('wisdom','智慧'),('charisma','魅力'),('luck','幸运')])+
                        f"\n本群今日已生成{status['generated']}/3个事件。发送“冒险”查看当前事件。")
            if op=='bag':
                items=game.status(player)['state']['inventory']
                return ('\n'.join('「'+item['name']+'」：'+item['nature'] for item in items[:10])+
                        (f'\n背包共{len(items)}件，本次展示前10件。' if len(items)>10 else '')) or '背包空空的。哼哼，先去冒险吧。'
            if op in ('start','boss'):
                result=game.start(player,request,final_boss=op=='boss')
            elif op=='retry':
                result=game.retry(player)
            else:
                result=game.act(player,request,action)
            reply=display(result)
            if result.get('next_event'):
                reply+='\n\n下一件冒险：\n'+display(result['next_event'])
            if result.get('next_notice'):
                reply+='\n'+result['next_notice']
            reply+=f"\n本群今日已生成{game.status(player)['generated']}/3个事件。"
            return reply
        except AdventureError as exc:
            return str(exc)+('可直接提交新方案，或发送“@我 游戏重试”。' if str(exc).startswith('行动评估失败') else '')
