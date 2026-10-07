"""Fixed CNY budget for the product support bot, shared across all its groups."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

DAILY_LIMIT = 5_000_000_000  # integer nanoyuan; never a UI/env setting
CASUAL_LIMIT = 3_000_000_000
TZ = timezone(timedelta(hours=8))
# Conservative peak CNY prices, checked 2026-10-05 against official pricing.
# Nanoyuan per token: cached input, uncached input, output. Off-peak is cheaper.
RATES = {
    'deepseek-flash': (40, 2000, 8000),
    'deepseek-v4-flash': (40, 2000, 8000),
    'deepseek-v4-flash-vision-exp': (40, 2000, 8000),
    'deepseek-v4-pro': (300, 9000, 27000),
}
PRICE_VERSION = '2026-10-05-peak-cny'


class BudgetExceeded(ValueError):
    pass


class SupportBudget:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(TZ))
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS support_costs(
                    id TEXT PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
                    model TEXT NOT NULL, price TEXT NOT NULL, reserve INTEGER NOT NULL,
                    cost INTEGER, state TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS support_cost_day ON support_costs(day);
            ''')

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

    def status(self):
        with self.connect() as db:
            day = self.day()
            row = db.execute('''SELECT coalesce(sum(cost),0) spent,
                coalesce(sum(CASE WHEN cost IS NULL THEN reserve ELSE 0 END),0) reserved,
                sum(CASE WHEN cost IS NULL THEN 1 ELSE 0 END) pending
                FROM support_costs WHERE day=?''', (day,)).fetchone()
            unresolved = db.execute('SELECT count(*) FROM support_costs WHERE cost IS NULL').fetchone()[0]
        spent, reserved = row['spent'], row['reserved']
        return dict(day=day, limit_yuan=5, spent_yuan=spent/1e9, reserved_yuan=reserved/1e9,
                    remaining_yuan=max(0, DAILY_LIMIT-spent-reserved)/1e9,
                    pending=row['pending'] or 0, unresolved=unresolved,
                    exhausted=spent+reserved >= DAILY_LIMIT,
                    pricing='按高峰单价保守估算，实际扣费以 DeepSeek 账单为准。')

    def reserve(self, payload, kind='technical'):
        if kind not in ('technical', 'casual', 'adventure'):
            raise ValueError('回复类型无效。')
        model = payload.get('model')
        if model not in RATES:
            raise ValueError('当前模型没有已核对的人民币价格，群助手暂停调用。请使用 deepseek-flash 或 deepseek-v4-pro。')
        maximum = payload.get('max_tokens')
        if type(maximum) is not int or not 1 <= maximum <= 4096:
            raise ValueError('群助手输出上限无效。')
        # Text-only requests: UTF-8 bytes conservatively bound byte-BPE tokens.
        # Include serialization, role framing and a generous per-message allowance.
        inputs = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        inputs += 128 * (1 + len(payload.get('messages', [])))
        _, miss, output = RATES[model]
        amount = inputs*miss + maximum*output
        key, day = uuid.uuid4().hex, self.day()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            total = db.execute('SELECT coalesce(sum(coalesce(cost,reserve)),0) FROM support_costs WHERE day=?', (day,)).fetchone()[0]
            casual = db.execute("SELECT coalesce(sum(coalesce(cost,reserve)),0) FROM support_costs WHERE day=? AND kind='casual'", (day,)).fetchone()[0]
            if total+amount > DAILY_LIMIT or (kind == 'casual' and casual+amount > CASUAL_LIMIT):
                raise BudgetExceeded('今日群助手额度不足，暂停模型回复；反馈仍保存在本地。')
            db.execute('INSERT INTO support_costs(id,day,kind,model,price,reserve,state) VALUES(?,?,?,?,?,?,?)',
                       (key, day, kind, model, PRICE_VERSION, amount, 'RESERVED'))
        return key

    def settle(self, key, usage):
        # Missing/invalid usage leaves the full reservation intact, including after restart.
        if not isinstance(usage, dict):
            return False
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        details = usage.get('prompt_tokens_details')
        cached = usage.get('prompt_cache_hit_tokens', details.get('cached_tokens', 0) if isinstance(details, dict) else 0)
        if any(type(v) is not int or v < 0 for v in (prompt, completion, cached)) or cached > prompt:
            return False
        if 'prompt_cache_miss_tokens' in usage and usage['prompt_cache_miss_tokens'] != prompt-cached:
            return False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM support_costs WHERE id=?', (key,)).fetchone()
            if not row:
                raise ValueError('费用预留不存在。')
            if row['cost'] is not None:
                return True
            hit, miss, output = RATES[row['model']]
            cost = cached*hit + (prompt-cached)*miss + completion*output
            # Never conceal provider usage greater than the reserved upper bound.
            db.execute("UPDATE support_costs SET cost=?,usage=?,state=? WHERE id=?",
                       (cost, json.dumps(usage), 'SETTLED' if cost <= row['reserve'] else 'OVER_BOUND', key))
        return True

    def uncertain(self, key):
        with self.connect() as db:
            db.execute("UPDATE support_costs SET state='UNKNOWN' WHERE id=? AND cost IS NULL", (key,))


def request_json(budget, config, payload, kind, transport=None):
    """One ledgered, tool-free DeepSeek call that must return a JSON object."""
    key = budget.reserve(payload, kind)
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10), trust_env=False,
                          follow_redirects=False, transport=transport) as client:
            with client.stream('POST', config['API_BASE'].rstrip('/')+'/chat/completions', json=payload,
                               headers={'Authorization':'Bearer '+config['API_KEY']}) as response:
                response.raise_for_status()
                raw = bytearray()
                for block in response.iter_bytes():
                    raw.extend(block)
                    if len(raw) > 2*1024*1024:
                        raise ValueError('模型响应过大，未发送。')
                data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('模型响应格式无效。')
        budget.settle(key, data.get('usage'))
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('模型答复未完整结束，已保留反馈，未发送。')
        result = json.loads(choice['message']['content'])
        if not isinstance(result, dict):
            raise ValueError('模型答复格式无效。')
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        budget.uncertain(key)
        raise ValueError('模型未返回可核对的完整答复，反馈保留，费用预留待核对；不会自动重试。') from None
