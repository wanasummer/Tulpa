"""Daily group events, individual heroes, independently accessible final boss."""
import copy
import json
import uuid

from .adventure import Adventure, AdventureError, validate_event, title, text


class SharedAdventure(Adventure):
    def __init__(self,*args,group='sandbox:group',**kwargs):
        super().__init__(*args,**kwargs)
        self.group=text(group,120)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS adventure_groups(
                id TEXT PRIMARY KEY,day TEXT NOT NULL,used INTEGER NOT NULL,current TEXT);
                CREATE TABLE IF NOT EXISTS adventure_group_events(
                id TEXT PRIMARY KEY,group_id TEXT NOT NULL,day TEXT NOT NULL,phase TEXT NOT NULL,
                data TEXT NOT NULL,claim TEXT);''')

    def _group(self,db):
        db.execute('INSERT OR IGNORE INTO adventure_groups VALUES(?,?,0,NULL)',(self.group,self.day()))
        row=db.execute('SELECT * FROM adventure_groups WHERE id=?',(self.group,)).fetchone()
        if row['day']!=self.day():
            # Expire old scenes and their actor claims without touching earned state.
            events=db.execute('SELECT id FROM adventure_group_events WHERE group_id=? AND day!=?',
                              (self.group,self.day())).fetchall()
            for event in events:
                db.execute("UPDATE adventure_group_events SET phase='expired' WHERE id=?",(event['id'],))
                claims=db.execute('SELECT id,data FROM adventure_attempts').fetchall()
                for claim in claims:
                    if json.loads(claim['data']).get('group_event')==event['id']:
                        db.execute('UPDATE adventure_players SET active=NULL WHERE active=?',(claim['id'],))
            db.execute('UPDATE adventure_groups SET day=?,used=0,current=NULL WHERE id=?',(self.day(),self.group))
            row=db.execute('SELECT * FROM adventure_groups WHERE id=?',(self.group,)).fetchone()
        return row

    def _player(self,db,player):
        row=super()._player(db,player)
        if row['active']:
            attempt=self._attempt(db,row['active'])
            if not attempt.get('group_event') and not attempt['event'].get('fixed_enemy'):
                # Retire the former solo mode's pending scene, preserving earned hero state.
                db.execute('UPDATE adventure_players SET active=NULL WHERE player=?',(player,))
                db.execute("UPDATE adventure_attempts SET phase='retired' WHERE id=?",(attempt['id'],))
                return super()._player(db,player)
            if attempt.get('group_event'):
                event=db.execute('SELECT phase FROM adventure_group_events WHERE id=?',(attempt['group_event'],)).fetchone()
                if event and event['phase'] in ('solved','expired'):
                    db.execute('UPDATE adventure_players SET active=NULL WHERE player=?',(player,))
                    row=super()._player(db,player)
        return row

    @staticmethod
    def _view(row):
        data=json.loads(row['data'])
        return dict(id=row['id'],day=row['day'],phase='ready' if row['phase']=='open' else row['phase'],
                    event=data['event'],action=None,result=None,group_event=row['id'])

    def status(self,player):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group=self._group(db)
            row=self._player(db,player)
            state=json.loads(row['state'])
            current=self._attempt(db,row['active']) if row['active'] else None
            if current is None and group['current']:
                current=self._view(db.execute('SELECT * FROM adventure_group_events WHERE id=?',(group['current'],)).fetchone())
            return dict(state=state,title=title(state['level']),remaining=3-group['used'],
                        current=current,group=self.group,generated=group['used'])

    def start(self,player,request_id,final_boss=False):
        if final_boss:
            return super().start(player,request_id,True)
        text(request_id,160)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group=self._group(db)
            hero=self._player(db,player)
            if hero['active']:
                return self._attempt(db,hero['active'])
            if group['current']:
                return self._view(db.execute('SELECT * FROM adventure_group_events WHERE id=?',(group['current'],)).fetchone())
            if group['used']>=3:
                raise AdventureError('本群今天的3个冒险已结束，明天再来。')
            state=json.loads(hero['state'])
            draw=self.rng.random()
            difficulty='weak' if draw<.15 else 'normal' if draw<.75 else 'hard' if draw<.95 else 'extreme'
            factor={'weak':.2,'normal':1,'hard':1.35,'extreme':1.8}[difficulty]
            event=dict(difficulty=difficulty,enemy_level=max(0,round((state['level']+2)*factor)),fixed_enemy=None,shared=True)
            data=dict(event=event,reference=state)
            eid=uuid.uuid4().hex
            db.execute('INSERT INTO adventure_group_events VALUES(?,?,?,?,?,NULL)',
                       (eid,self.group,self.day(),'generating',json.dumps(data)))
            db.execute('UPDATE adventure_groups SET current=?,used=used+1 WHERE id=?',(eid,self.group))
        return self._generate_group(eid,data)

    def _generate_group(self,eid,data):
        try:
            event=validate_event(self.model('event',dict(player=data['reference'],rules=data['event'],
                shared=True,context='这是群共享事件，任何勇者均可参加；不能把参考玩家的物品写成场景必需条件。')),None)
            data['event'].update(event)
        except Exception:
            with self.connect() as db:
                db.execute("UPDATE adventure_group_events SET phase='generation_error' WHERE id=? AND phase='generating'",(eid,))
            raise AdventureError('共享事件生成失败，发送“重试”恢复；不会重新占用群次数。') from None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._group(db)
            changed=db.execute("UPDATE adventure_group_events SET phase='open',data=? WHERE id=? AND phase='generating'",
                               (json.dumps(data,ensure_ascii=False),eid)).rowcount
            row=db.execute('SELECT * FROM adventure_group_events WHERE id=?',(eid,)).fetchone()
            if not changed:
                raise AdventureError('旧事件已过期，请查看今天的新冒险。')
            return self._view(row)

    def act(self,player,request_id,action,flee=False):
        if flee or action.strip().startswith('逃跑'):
            raise AdventureError('现在没有逃跑选项，可以暂不参加，未解决的事件明天自动刷新。')
        action=text(action,1500)
        text(request_id,160)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group=self._group(db)
            hero=self._player(db,player)
            duplicate=db.execute('SELECT attempt FROM adventure_actions WHERE player=? AND request_id=?',
                                 (player,request_id)).fetchone()
            if duplicate:
                return self._attempt(db,duplicate['attempt'])
            personal=self._attempt(db,hero['active']) if hero['active'] else None
            if personal and personal.get('group_event') and personal['phase']=='evaluation_error':
                # A new human message is a new solution, not a replay of the failed call.
                # Preserve the old diagnostic and don't penalize the hero for an API/schema error.
                db.execute('UPDATE adventure_players SET active=NULL WHERE player=?',(player,))
                personal=None
            if personal and not personal.get('group_event'):
                boss=True
            else:
                boss=False
                if personal:
                    raise AdventureError('你有未完成的请求，请先重试或等待处理。')
                if not group['current']:
                    raise AdventureError('请先输入“冒险”获取本群事件。')
                shared=db.execute('SELECT * FROM adventure_group_events WHERE id=?',(group['current'],)).fetchone()
                if shared['phase']!='open':
                    raise AdventureError('共享事件正在生成或有人正在挑战，请稍后再看。')
                data=json.loads(shared['data'])
                attempt=dict(id=uuid.uuid4().hex,phase='evaluating',day=self.day(),
                             event=data['event'],before=json.loads(hero['state']),roll=self.rng.random(),
                             action=action,flee=False,result=None,group_event=shared['id'])
                db.execute('INSERT INTO adventure_attempts VALUES(?,?,?,?,?)',
                    (attempt['id'],player,'shared:'+request_id,'evaluating',json.dumps({k:v for k,v in attempt.items() if k not in ('id','phase')})))
                db.execute('INSERT INTO adventure_actions VALUES(?,?,?)',(player,request_id,attempt['id']))
                db.execute('UPDATE adventure_players SET active=? WHERE player=?',(attempt['id'],player))
                db.execute("UPDATE adventure_group_events SET phase='evaluating',claim=? WHERE id=?",(attempt['id'],shared['id']))
        return super().act(player,request_id,action) if boss else self._finish(player,attempt)

    def _finish(self,player,attempt):
        try:
            result=self._evaluate(player,attempt)
        except AdventureError:
            with self.connect() as db:
                phase=db.execute('SELECT phase FROM adventure_attempts WHERE id=?',(attempt['id'],)).fetchone()[0]
                if phase=='evaluation_error':
                    db.execute("UPDATE adventure_group_events SET phase='open',claim=NULL WHERE claim=?",(attempt['id'],))
            raise
        if result['result']['outcome']=='success':
            # Only success advances. The last success does not generate a fourth event.
            try:
                result['next_event']=self.start(player,'next:'+attempt['id'])
            except AdventureError as exc:
                result['next_notice']=str(exc)
        return result

    def _settle_extra(self,db,player,attempt,success):
        if not attempt.get('group_event'):
            return
        self._group(db)
        row=db.execute('SELECT * FROM adventure_group_events WHERE id=?',(attempt['group_event'],)).fetchone()
        if row['day']!=self.day() or row['phase']!='evaluating' or row['claim']!=attempt['id']:
            raise AdventureError('此事件已过期或已由其他群友解决，不能重复结算。')
        db.execute('UPDATE adventure_group_events SET phase=?,claim=NULL WHERE id=?',
                   ('solved' if success else 'open',row['id']))
        if success:
            db.execute('UPDATE adventure_groups SET current=NULL WHERE id=?',(self.group,))

    def retry(self,player):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group=self._group(db)
            hero=self._player(db,player)
            attempt=self._attempt(db,hero['active']) if hero['active'] else None
            if attempt and attempt.get('group_event'):
                event=db.execute('SELECT * FROM adventure_group_events WHERE id=?',(attempt['group_event'],)).fetchone()
                if attempt['phase']!='evaluation_error' or event['phase']!='open':
                    raise AdventureError('请求正在处理或事件已经结束，不能重试。')
                self._save(db,attempt,'evaluating')
                db.execute("UPDATE adventure_group_events SET phase='evaluating',claim=? WHERE id=?",(attempt['id'],event['id']))
                mode='action'
            elif not attempt and group['current']:
                event=db.execute('SELECT * FROM adventure_group_events WHERE id=?',(group['current'],)).fetchone()
                if event['phase']!='generation_error':
                    raise AdventureError('没有可以重试的事件。')
                db.execute("UPDATE adventure_group_events SET phase='generating' WHERE id=?",(event['id'],))
                mode='generate'
                data=json.loads(event['data'])
            else:
                mode='personal'
        if mode=='action':
            return self._finish(player,attempt)
        if mode=='generate':
            return self._generate_group(event['id'],data)
        return super().retry(player)
