"""Bounded product-source index; group documents retain their group scope."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

SKIP = {'.git', '.venv', 'node_modules', 'target', 'dist', 'build', '__pycache__',
        '.cache', '.tmp', 'data', 'logs', 'reports', 'release', 'tests', 'test', 'vendor',
        'electron'}
SOURCE_TYPES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.vue', '.svelte', '.html', '.css', '.rs', '.toml'}
UI_TYPES = {'.html', '.svelte', '.vue'}
FRONTENDS = {'desktop-tauri', 'frontend', 'src', 'web'}
MAX_FILES = 5000
MAX_FILE = 512*1024
SECRET = re.compile(r'(?im)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)([\"\']).*?\2')
CJK = re.compile(r'[\u3400-\u9fff]')
LITERAL = re.compile(r'"([^"\\\n]*)"|\'([^\'\\\n]*)\'|`([^`\\$]*)`')
VISIBLE_ATTRS = {'title', 'placeholder', 'alt', 'aria-label', 'label', 'hint', 'description', 'tooltip'}


def redact(text):
    text = SECRET.sub(r'\1"[已遮盖]"', text)
    return re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b', '[已遮盖]', text)


def _template_expressions(markup):
    # Replace {expression} with its Chinese string literals; \x01 marks the
    # substitution so it stays a valid attribute value and is removed later.
    parts, last, i, n = [], 0, 0, len(markup)
    while i < n:
        if markup[i] != '{':
            i += 1
            continue
        depth, j, quote = 0, i, ''
        while j < n:
            ch = markup[j]
            if quote:
                if ch == '\\':
                    j += 1
                elif ch == quote:
                    quote = ''
            elif ch in '"\'`':
                quote = ch
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if not depth:
                    break
            j += 1
        if j >= n:
            break
        literals = (next(g for g in m.groups() if g is not None) for m in LITERAL.finditer(markup[i:j+1]))
        visible = ' '.join(t.replace('"', '') for t in literals if CJK.search(t))
        parts.append(markup[last:i]+'"\x01'+visible+'\x01"')
        last = i = j+1
    parts.append(markup[last:])
    return ''.join(parts)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []

    def add(self, text):
        text = ' '.join(re.sub(r'"?\x01"?', ' ', text).split())
        if re.search(r'[\w\u3400-\u9fff]', text) and (not self.lines or self.lines[-1] != text):
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if value and not name.startswith(('on', ':', '@', 'v-', 'bind:', 'class', 'style')) and (
                    name in VISIBLE_ATTRS or CJK.search(value)):
                self.add(value)

    def handle_data(self, data):
        self.add(data)


def visible_text(markup, template=False):
    """Text a user can see in HTML/Svelte/Vue markup; scripts and styles are dropped."""
    markup = re.sub(r'(?is)<(script|style)\b[^>]*>.*?</\1\s*>|<!--.*?-->', ' ', markup)
    if template:
        markup = _template_expressions(markup)
    parser = _VisibleText()
    parser.feed(markup)
    parser.close()
    return '\n'.join(parser.lines)


class SupportKnowledge:
    def __init__(self, path, project_root):
        self.path, self.project_root = Path(path), Path(project_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS support_knowledge(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
                    group_id TEXT NOT NULL, text TEXT NOT NULL, locator TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS support_knowledge_fts USING fts5(id UNINDEXED, words);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def replace(self, rows, group_id='', complete=True):
        from .store import tokens
        with self.connect() as db:
            sources = list(dict.fromkeys(row[0] for row in rows))
            where, values = 'group_id=?', [group_id]
            if not complete:
                if not sources:
                    return
                where += ' AND source IN ('+','.join('?' for _ in sources)+')'
                values += sources
            ids = [r[0] for r in db.execute('SELECT id FROM support_knowledge WHERE '+where, values)]
            db.executemany('DELETE FROM support_knowledge_fts WHERE id=?', [(i,) for i in ids])
            db.execute('DELETE FROM support_knowledge WHERE '+where, values)
            for source, kind, text, locator in rows:
                text = redact(text)
                for offset in range(0, len(text), 1400):
                    part = text[offset:offset+1600]
                    key = hashlib.sha256(f'{group_id}\0{source}\0{locator}\0{offset}'.encode()).hexdigest()
                    db.execute('INSERT INTO support_knowledge VALUES(?,?,?,?,?,?)',
                               (key, source, kind, group_id, part, f'{locator} · 字符 {offset}'))
                    db.execute('INSERT INTO support_knowledge_fts VALUES(?,?)', (key, tokens(source+' '+part)))

    def refresh_project(self):
        root = self.project_root.resolve()
        if not root.is_dir():
            raise ValueError('未找到 ASMRTranslator 项目目录，请填写本机项目路径。')
        rows, skipped, indexed = [], 0, 0
        # Do not traverse excluded trees or symlinks, including directory junctions.
        import os
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')
                       and not Path(folder, d).is_symlink() and not os.path.islink(Path(folder, d))
                       and not (getattr(Path(folder, d).stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400)
                       and not (hasattr(os.path, 'isjunction') and os.path.isjunction(Path(folder, d)))]
            for name in sorted(files):
                file = Path(folder, name)
                rel = file.relative_to(root)
                is_readme = name.lower().startswith('readme') and file.suffix.lower() == '.md'
                is_code = file.suffix.lower() in SOURCE_TYPES and (
                    rel.parts[0] in FRONTENDS or rel.parts[0] == 'modules' or name == 'main.py')
                if not (is_readme or is_code) or file.is_symlink() or name.startswith('.'):
                    continue
                if not file.resolve().is_relative_to(root) or file.stat().st_size > MAX_FILE:
                    skipped += 1
                    continue
                if indexed >= MAX_FILES:
                    skipped += 1
                    continue
                try:
                    text = file.read_text(encoding='utf-8')
                except (OSError, UnicodeError):
                    skipped += 1
                    continue
                if text.strip():
                    indexed += 1
                    rows.append((rel.as_posix(), 'readme' if is_readme else 'code', text, '源码/说明'))
                    suffix = file.suffix.lower()
                    # Markup outside scripts is what users see in the product, so it is public.
                    ui = visible_text(text, suffix != '.html') if is_code and suffix in UI_TYPES else ''
                    if ui:
                        rows.append((rel.as_posix(), 'ui', ui, '界面文字'))
        if not rows:
            raise ValueError('目录中未发现 README 或前后端源码，原知识索引保留。')
        self.replace(rows)
        return dict(files=indexed, skipped=skipped, **self.status())

    def refresh_group(self, store, cid, name, cancel=None):
        from .artifact_onebot import OneBot
        from .artifacts import Artifacts
        from .retrieval import Plan
        OneBot().reconcile(store, cid, name)
        artifacts = Artifacts(store)
        with store.connect() as db:
            sources = db.execute("""SELECT id,filename FROM artifact_sources
                WHERE platform='qq' AND conversation_id=? AND remote_status!='absent'
                AND extension IN ('md','txt','pdf','docx','pptx','csv','rst') ORDER BY timestamp DESC,id DESC LIMIT 41""", (cid,)).fetchall()
        rows, errors = [], []
        for source in sources[:40]:
            if cancel and cancel.is_set():
                raise ValueError('文档读取已停止，原群知识保留。')
            try:
                artifacts.prepare(source['id'])
                row = artifacts.get(source['id'], Plan(platforms=['qq'], conversations=[['qq', cid]]))
                if row['parse_status'] != 'PARSED':
                    errors.append(f"{source['filename']}：正文未完整解析，保留原知识")
                    continue
                with store.connect() as db:
                    chunks = db.execute('SELECT text,locator FROM artifact_chunks WHERE sha256=? ORDER BY ordinal LIMIT 1200', (row['sha256'],)).fetchall()
                for chunk in chunks:
                    rows.append((f"群文档/F{source['id']}/{source['filename']}", 'group_document', chunk['text'], chunk['locator']))
                if not chunks:
                    errors.append(f"{source['filename']}：没有可检索正文")
            except ValueError as exc:
                errors.append(f"{source['filename']}：{exc}")
        if len(sources) > 40:
            errors.append('本次只读取最近40份支持的文档。')
        if errors:
            # Successful files can be updated without deleting older files that
            # failed or were outside this pass's bounded processing window.
            self.replace(rows, cid, complete=False)
            return dict(documents=len(sources[:40]), updated=bool(rows), partial=True, errors=errors, **self.status())
        self.replace(rows, cid)
        return dict(documents=len(sources), updated=True, partial=False, errors=errors, **self.status())

    def search(self, question, cid='', limit=8, public_only=False):
        from .store import tokens
        words = list(dict.fromkeys(w for w in tokens(question).split() if len(w) > 1))[:24]
        if not words:
            return []
        expression = ' OR '.join('"'+w.replace('"', '""')+'"' for w in words)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            restriction = " AND k.kind!='code'" if public_only else ''
            rows = db.execute('''SELECT k.*,bm25(support_knowledge_fts) score FROM support_knowledge_fts
                JOIN support_knowledge k ON k.id=support_knowledge_fts.id
                WHERE support_knowledge_fts MATCH ? AND k.group_id IN ('',?)'''+restriction+
                ' ORDER BY score LIMIT ?', (expression, cid, limit)).fetchall()
        return [dict(row) for row in rows]

    def status(self):
        with self.connect() as db:
            chunks = db.execute('SELECT count(*) FROM support_knowledge').fetchone()[0]
            sources = db.execute('SELECT count(DISTINCT group_id||source) FROM support_knowledge').fetchone()[0]
            project_sources = db.execute("SELECT count(DISTINCT source) FROM support_knowledge WHERE group_id='' ").fetchone()[0]
        return dict(project_root=str(self.project_root), chunks=chunks, sources=sources, project_sources=project_sources)
