"""Isolated adventure prototype: persistent JSON players and atomic settlement."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy
import json
import secrets
import sqlite3
import uuid

TZ = timezone(timedelta(hours=8))
ATTRIBUTES = ('strength', 'agility', 'wisdom', 'charisma', 'luck')
TITLES = {20:'初露锋芒的勇者', 30:'王国认证勇者', 50:'异世界传奇',
          70:'魔王的头号麻烦', 100:'大肥鱼钦定救世主'}
FINAL_BOSS = {
    'name':'灭世龙王·烬冠', 'level':120,
    'attributes':dict(strength=160, agility=90, wisdom=110, charisma=80, luck=60),
    'skills':['鳞甲抵御正面物理攻击', '龙息焚烧正面区域，喷吐后短暂冷却',
              '狂怒阶段攻击增强，胸口核心暴露'],
    'weaknesses':{'water_cooling':'水能降低龙息威力，但蒸汽遮蔽双方视野',
                  'overheat_timing':'喷吐后的冷却间隙可接近',
                  'scale_gap':'狂怒时胸口核心失去鳞甲保护'},
}


class AdventureError(ValueError):
    pass


def attributes(level):
    return {key:5 + level for key in ATTRIBUTES}


def initial():
    return dict(level=0, attributes=attributes(0), inventory=[], reincarnation=1)


def title(level):
    return next((TITLES[n] for n in sorted(TITLES, reverse=True) if level >= n), '初来乍到')


def text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AdventureError('模型返回了无效文本。')
    return value.strip()


def validate_event(raw, fixed):
    if not isinstance(raw, dict):
        raise AdventureError('模型事件格式无效。')
    result = {key:text(raw.get(key), maximum) for key, maximum in
              (('title',80), ('scene',1200), ('enemy',80))}
    for key in ('traits', 'clues'):
        values = raw.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= 4:
            raise AdventureError('事件需要敌人特点和场景线索。')
        result[key] = [text(v,200) for v in values]
    if fixed:
        result['enemy'] = fixed['name']
        result['traits'] = list(fixed['skills'])
        result['clues'] = list(fixed['weaknesses'].values())
    return result


def validate_evaluation(raw, state, fixed):
    if not isinstance(raw, dict):
        raise AdventureError('行动评估格式无效。')
    result = {key:raw.get(key) for key in ('approach','plausibility','attribute','item_id','item_help','exploit')}
    # Some responses mistakenly put causal impossibility in both fields.
    # Preserve the zero success probability while fixing only this known mix-up.
    if result['approach']=='impossible' and result['plausibility']=='impossible':
        result['approach']='absurd'
    if (result['approach'] not in ('normal','absurd')
            or result['plausibility'] not in ('plausible','strained','impossible')
            or result['attribute'] not in ATTRIBUTES
            or type(result['item_help']) is not int or result['item_help'] not in (0,1)):
        raise AdventureError('行动评估枚举或数值无效。')
    ids = [item['id'] for item in state['inventory']]
    if result['item_id'] is not None and result['item_id'] not in ids:
        raise AdventureError('模型使用了背包之外的物品。')
    if result['item_help'] and result['item_id'] is None:
        raise AdventureError('物品加成缺乏实际物品。')
    if result['exploit'] is not None and (not fixed or result['exploit'] not in fixed['weaknesses']):
        raise AdventureError('模型捏造了 Boss 弱点。')
    result['reason'] = text(raw.get('reason'),300)
    reward = raw.get('reward')
    if not isinstance(reward, dict):
        raise AdventureError('模型奖励格式无效。')
    result['reward'] = dict(name=text(reward.get('name'),80), nature=text(reward.get('nature'),250))
    return result


def probability(state, event, evaluation, flee=False):
    """All numeric authority stays here, never in the generated JSON."""
    if evaluation['plausibility'] == 'impossible':
        return 0
    attr = state['attributes'][evaluation['attribute']]
    target = 5 + event['enemy_level']
    advantage = max(-.6, min(.6, (attr-target) / max(target,10) * .45))
    base = .75 if flee else (.85 if evaluation['approach'] == 'normal' else .4)
    if evaluation['plausibility'] == 'strained':
        base = .25 if evaluation['approach'] == 'normal' else .12
    chance = base + advantage + .15 * evaluation['item_help']
    # Luck helps absurdity and escape, but cannot erase impossible causality.
    if evaluation['approach'] == 'absurd' or flee:
        chance += min(.08, max(0, state['attributes']['luck']-target) / max(target,10)*.05)
    if event['fixed_enemy'] and not flee:
        if not evaluation['exploit']:
            chance = min(chance,.1)
        else:
            chance += .15
    return round(max(.02, min(.98,chance)),4)


class Adventure:
    def __init__(self, path, model, clock=None, rng=None):
        self.path, self.model = Path(path), model
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self.rng = rng or secrets.SystemRandom()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS adventure_players(
                    player TEXT PRIMARY KEY, state TEXT NOT NULL, day TEXT NOT NULL,
                    used INTEGER NOT NULL, active TEXT);
                CREATE TABLE IF NOT EXISTS adventure_attempts(
                    id TEXT PRIMARY KEY, player TEXT NOT NULL, request_id TEXT NOT NULL,
                    phase TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(player,request_id));
                CREATE TABLE IF NOT EXISTS adventure_actions(
                    player TEXT NOT NULL, request_id TEXT NOT NULL, attempt TEXT NOT NULL,
                    PRIMARY KEY(player,request_id));''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def day(self):
        return self.clock().astimezone(TZ).date().isoformat()

    def _player(self, db, player):
        text(player,120)
        db.execute('INSERT OR IGNORE INTO adventure_players VALUES(?,?,?,0,NULL)',
                   (player,json.dumps(initial()),self.day()))
        row = db.execute('SELECT * FROM adventure_players WHERE player=?', (player,)).fetchone()
        return row

    @staticmethod
    def _attempt(db, attempt):
        row = db.execute('SELECT * FROM adventure_attempts WHERE id=?',(attempt,)).fetchone()
        return dict(id=row['id'], phase=row['phase'], **json.loads(row['data']))

    @staticmethod
    def _save(db, attempt, phase):
        payload = {k:v for k,v in attempt.items() if k not in ('id','phase')}
        db.execute('UPDATE adventure_attempts SET phase=?,data=? WHERE id=?',
                   (phase,json.dumps(payload,ensure_ascii=False),attempt['id']))

    def status(self, player):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._player(db,player)
            state = json.loads(row['state'])
            return dict(state=state,title=title(state['level']), remaining=3-(row['used'] if row['day']==self.day() else 0),
                        current=self._attempt(db,row['active']) if row['active'] else None)

    def start(self, player, request_id, final_boss=False):
        text(request_id,160)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._player(db,player)
            duplicate = db.execute('SELECT id FROM adventure_attempts WHERE player=? AND request_id=?',
                                   (player,request_id)).fetchone()
            if duplicate:
                return self._attempt(db,duplicate['id'])
            if row['active']:
                return self._attempt(db,row['active'])
            state = json.loads(row['state'])
            if final_boss and state['level'] < 100:
                raise AdventureError('100级才能挑战最终 Boss。')
            used = row['used'] if row['day']==self.day() else 0
            if used >= 3 and not final_boss:
                raise AdventureError('今天的3次冒险已经用完。')
            draw = self.rng.random()
            difficulty = 'weak' if draw < .15 else 'normal' if draw < .75 else 'hard' if draw < .95 else 'extreme'
            factor = {'weak':.2,'normal':1,'hard':1.35,'extreme':1.8}[difficulty]
            fixed = copy.deepcopy(FINAL_BOSS) if final_boss else None
            event = dict(difficulty='extreme' if fixed else difficulty,
                         enemy_level=fixed['level'] if fixed else max(0,round((state['level']+2)*factor)),
                         fixed_enemy=fixed)
            attempt = dict(id=uuid.uuid4().hex, phase='generating', event=event, before=state,
                           day=self.day(), roll=self.rng.random(), action=None, result=None)
            db.execute('INSERT INTO adventure_attempts VALUES(?,?,?,?,?)',
                       (attempt['id'],player,request_id,'generating',json.dumps({k:v for k,v in attempt.items() if k not in ('id','phase')})))
            db.execute('UPDATE adventure_players SET used=?,day=?,active=? WHERE player=?',
                       (used if final_boss else used+1,self.day(),attempt['id'],player))
        return self._generate(attempt)

    def _generate(self, attempt):
        try:
            raw = self.model('event',dict(player=attempt['before'], rules=attempt['event']))
            attempt['event'].update(validate_event(raw,attempt['event']['fixed_enemy']))
        except Exception:
            with self.connect() as db:
                self._save(db,attempt,'generation_error')
            raise AdventureError('事件生成失败；次数和事件预留已保存，需显式重试，不自动调用模型。') from None
        with self.connect() as db:
            self._save(db,attempt,'ready')
        attempt['phase'] = 'ready'
        return attempt

    def act(self, player, request_id, action, flee=False):
        text(request_id,160)
        action = text(action,1500)
        if type(flee) is not bool:
            raise AdventureError('逃跑标记无效。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._player(db,player)
            duplicate = db.execute('SELECT attempt FROM adventure_actions WHERE player=? AND request_id=?',
                                   (player,request_id)).fetchone()
            if duplicate:
                return self._attempt(db,duplicate['attempt'])
            if not row['active']:
                raise AdventureError('请先开始一次冒险。')
            attempt = self._attempt(db,row['active'])
            if attempt['phase'] != 'ready':
                raise AdventureError('事件正在处理或等待恢复，不能重复行动。')
            attempt['action'], attempt['flee'] = action,flee
            db.execute('INSERT INTO adventure_actions VALUES(?,?,?)',(player,request_id,attempt['id']))
            self._save(db,attempt,'evaluating')
        return self._evaluate(player,attempt)

    def retry(self, player):
        """Only explicit retry of known errors; in-flight/uncertain calls are never replayed."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._player(db,player)
            if not row['active']:
                raise AdventureError('没有需要重试的事件。')
            attempt = self._attempt(db,row['active'])
            phase = attempt['phase']
            if phase not in ('generation_error','evaluation_error'):
                raise AdventureError('不能重放正在处理或结果不确定的模型请求。')
            self._save(db,attempt,'generating' if phase=='generation_error' else 'evaluating')
        return self._generate(attempt) if phase=='generation_error' else self._evaluate(player,attempt)

    def _evaluate(self, player, attempt):
        try:
            evaluation = validate_evaluation(self.model('evaluate',dict(
                player=attempt['before'], event=attempt['event'], action=attempt['action'], flee=attempt['flee'])),
                attempt['before'],attempt['event']['fixed_enemy'])
        except Exception as exc:
            with self.connect() as db:
                attempt['evaluation_error_detail']={'type':type(exc).__name__,
                    'reason':str(exc)[:300] if isinstance(exc,AdventureError) else '模型请求未返回完整可验证结果。'}
                self._save(db,attempt,'evaluation_error')
            raise AdventureError('行动评估失败；原行动和随机数已保存，未扣等级或物品。') from None
        chance = probability(attempt['before'],attempt['event'],evaluation,attempt['flee'])
        success = attempt['roll'] < chance
        state = copy.deepcopy(attempt['before'])
        gain, reward = 0,None
        if not success:
            state = initial()
            state['reincarnation'] = attempt['before']['reincarnation']+1
        elif not attempt['flee']:
            gain = {'weak':1,'normal':1,'hard':2,'extreme':3}[attempt['event']['difficulty']]
            state['level'] += gain
            state['attributes'] = attributes(state['level'])
            reward = dict(id=uuid.uuid4().hex, **evaluation['reward'],
                          origin=attempt['event']['enemy'],
                          rarity='奇珍' if evaluation['approach']=='absurd' else '普通')
            # Bounded backpack keeps model context and storage predictable.
            if len(state['inventory']) < 30:
                state['inventory'].append(reward)
            else:
                reward = None
        outcome = ('escaped' if attempt['flee'] else 'success') if success else 'death'
        attempt['evaluation'] = evaluation
        attempt['result'] = dict(outcome=outcome, probability=chance, roll=attempt['roll'],
                                 gain=gain, before_level=attempt['before']['level'], level=state['level'],
                                 title=title(state['level']), reward=reward, absurd=evaluation['approach']=='absurd',
                                 story='你脱离了危险。' if outcome=='escaped' else
                                       '你通过了挑战。' if success else '这次冒险结束了，勇者重新转生。')
        # Commit numeric result before narration. A crash cannot award twice.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT phase FROM adventure_attempts WHERE id=?',(attempt['id'],)).fetchone()
            if current['phase'] != 'evaluating':
                raise AdventureError('事件已结算，不能重复发奖。')
            self._settle_extra(db,player,attempt,success)
            db.execute('UPDATE adventure_players SET state=?,active=NULL WHERE player=? AND active=?',
                       (json.dumps(state,ensure_ascii=False),player,attempt['id']))
            self._save(db,attempt,'settled')
        try:
            # No roll is exposed until after the decision is committed.
            raw = self.model('narrate',dict(event=attempt['event'], action=attempt['action'],
                                          evaluation=evaluation,
                                          result={k:v for k,v in attempt['result'].items() if k!='story'}))
            attempt['result']['story'] = text(raw.get('story'),350)
        except Exception:
            attempt['result']['narration_fallback'] = True
        with self.connect() as db:
            self._save(db,attempt,'settled')
        attempt['phase'] = 'settled'
        return attempt

    def _settle_extra(self,db,player,attempt,success):
        pass


def display(attempt):
    if attempt['phase'] == 'ready':
        event = attempt['event']
        difficulty={'weak':'杂鱼','normal':'普通','hard':'困难','extreme':'极难'}[event['difficulty']]
        return f"【{event['title']}】\n{event['scene']}\n敌人 Lv.{event['enemy_level']}｜{difficulty}\n你打算怎么做？"
    if attempt['phase'] != 'settled':
        return '事件已保存，等待处理或显式恢复。'
    result = attempt['result']
    label = {'success':'挑战成功','escaped':'逃跑成功','death':'失败，重新转生'}[result['outcome']]
    reward = '\n获得「'+result['reward']['name']+'」' if result['reward'] else ''
    return (f"{result['story']}\n{label}｜Lv.{result['before_level']} → Lv.{result['level']}"
            f"｜+{result['gain']}级\n当前称号：{result['title']}"+reward)
