# -*- coding: utf-8 -*-
"""共享库：纯标准库解析 xlsx + 通用工具。"""
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

from project_paths import default_db_path

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
T_TAG = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'
R_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
ROW_TAG = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'

# SQLite 镜像始终位于动态发现的工程根目录，不依赖盘符或固定目录层级。
DEFAULT_DB = str(default_db_path())


def _col_letter(ref):
    return re.match(r'([A-Z]+)', ref).group(1)


def _col_index(letter):
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(z):
    try:
        sst_xml = z.read('xl/sharedStrings.xml')
        sst = ET.fromstring(sst_xml)
        return [''.join(t.text or '' for t in si.iter(T_TAG))
                for si in sst.findall('m:si', NS)]
    except KeyError:
        return []


def _sheet_target(z, sheet_name):
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rid2target = {r.get('Id'): r.get('Target') for r in rels}
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    target = None
    for s in wb.find('m:sheets', NS):
        if s.get('name') == sheet_name:
            target = rid2target[s.get(R_ID)]
            break
    if target is None:
        names = [s.get('name') for s in wb.find('m:sheets', NS)]
        raise ValueError(f"找不到工作表 {sheet_name!r}，可用: {names}")
    target = target.lstrip('/')
    if not target.startswith('xl/'):
        target = 'xl/' + target
    return target


def iter_sheet_rows(xlsx_path, sheet_name):
    """流式读取工作表，逐行 yield list[str]，避免把大表 XML 整页载入内存。"""
    with zipfile.ZipFile(xlsx_path) as z:
        strings = _shared_strings(z)
        target = _sheet_target(z, sheet_name)

        def cellval(c):
            v = c.find('m:v', NS)
            if v is None:
                ist = c.find('m:is', NS)
                if ist is not None:
                    return ''.join(t.text or '' for t in ist.iter(T_TAG))
                return ''
            if c.get('t') == 's':
                return strings[int(v.text)]
            return v.text or ''

        with z.open(target) as stream:
            for _event, elem in ET.iterparse(stream, events=('end',)):
                if elem.tag != ROW_TAG:
                    continue
                cells = {}
                maxi = -1
                for c in elem.findall('m:c', NS):
                    ref = c.get('r')
                    if not ref:
                        continue
                    i = _col_index(_col_letter(ref))
                    raw = cellval(c)
                    val = raw.strip() if raw else ''
                    if val:
                        cells[i] = val
                        maxi = max(maxi, i)
                yield [cells.get(i, '') for i in range(maxi + 1)]
                elem.clear()


def read_sheet(xlsx_path, sheet_name):
    """兼容接口：读取整页。大表同步应优先使用 iter_sheet_rows。"""
    return list(iter_sheet_rows(xlsx_path, sheet_name))


def sheet_names(xlsx_path):
    with zipfile.ZipFile(xlsx_path) as z:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        return [s.get('name') for s in wb.find('m:sheets', NS)]


def norm_code(v):
    """规整编码字段：数字型 '1.0'->'01' 等，保持 2 位；已是字符串则原样。"""
    if v is None:
        return ''
    v = str(v).strip()
    if not v:
        return ''
    if re.fullmatch(r'\d+\.0', v):
        v = v[:-2]
    if v.isdigit() and len(v) == 1:
        v = '0' + v
    return v


def connect(db_path=DEFAULT_DB):
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY,
        excel_row INTEGER,          -- Excel 行号（第一页）
        col_a TEXT, col_b TEXT, col_c TEXT, col_d TEXT, col_e TEXT,
        col_f TEXT, col_g TEXT, col_h TEXT, col_i TEXT, col_j TEXT, col_k TEXT,
        name TEXT, spec TEXT, unit TEXT, usage TEXT,   -- M/N/O/S 辅助检索列
        retired INTEGER DEFAULT 0   -- 历史兼容字段；当前只同步第一工作表，固定为 0
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_j ON materials(col_j)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_h ON materials(col_h)")
    con.execute("""CREATE TABLE IF NOT EXISTS meta(
        key TEXT PRIMARY KEY, value TEXT)""")
    return con


# materials 表列序（与 connect() 中建表一致）
SELECT_ALL = ("id,excel_row,col_a,col_b,col_c,col_d,col_e,col_f,col_g,col_h,"
              "col_i,col_j,col_k,name,spec,unit,usage,retired")


def bigrams(s):
    s = re.sub(r'\s+', '', s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def tokens(s):
    return [t for t in re.split(r'[\s,，、;；/\\|()（）\[\]【】*×xX-]+', s.lower()) if t]


def score(query, qgrams, qtokens, text):
    if not text:
        return 0.0
    t = text.lower()
    hit = sum(1 for tok in qtokens if tok and tok in t)
    cover = hit / len(qtokens) if qtokens else 0
    tg = bigrams(text)
    jac = len(qgrams & tg) / len(qgrams | tg) if (qgrams | tg) else 0
    seq = SequenceMatcher(None, query.lower(), t).ratio()
    return cover * 0.5 + jac * 0.3 + seq * 0.2


def search_top(con, query):
    """在在用物料中模糊检索，固定返回 Top5 候选 [(score, rowtuple), ...]。

    rowtuple 顺序见 SELECT_ALL（含 col_a，共 18 列）。
    评分 = 名称 + 规格 + 类别路径 + 用途，按 H(六位类别) 去重保证多样性。
    """
    top = 5
    rows = con.execute(
        f"SELECT {SELECT_ALL} FROM materials WHERE retired=0"
    ).fetchall()
    if not rows:
        return []
    q = query.strip()
    qg, qt = bigrams(q), tokens(q)
    scored = []
    for r in rows:
        name, spec, usage = r[13], r[14], r[16]
        b, d, f = r[3], r[5], r[7]
        s = (score(q, qg, qt, name) * 1.0
             + score(q, qg, qt, spec) * 0.6
             + score(q, qg, qt, f'{b}{d}{f}') * 0.5
             + score(q, qg, qt, usage) * 0.2)
        if s > 0.02:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])

    # 多样性选取：同一 H 类别最多 2 条，凑不够再放宽
    picked, per_h = [], {}
    for s, r in scored:
        h = r[9]
        if per_h.get(h, 0) >= 2:
            continue
        picked.append((s, r))
        per_h[h] = per_h.get(h, 0) + 1
        if len(picked) >= top:
            break
    if len(picked) < top:
        chosen = {r[0] for _, r in picked}
        for s, r in scored:
            if r[0] not in chosen:
                picked.append((s, r))
                if len(picked) >= top:
                    break
    return picked


def generate_codes(con, ref, start_seq=None):
    """按参考行 ref（SELECT_ALL 顺序元组）生成一个新物料编码。

    start_seq: 可选，本运行内已为该 C/E/G 序列分配的续号起点；
               用于同一序列多条申请连续分配，避免重复流水号。
               最终生效基准 = max(DB 当前最大流水号, start_seq)。
    返回 dict: prefix/c/e/g/b/d/f/h/k/a/ref_*/max_seq/total/codes
    """
    (_, _, a, b, c, d, e, f, g, h, i, j, k,
     rname, rspec, runit, rusage, _) = ref
    prefix = f"{c}{e}{g}"
    row = con.execute(
        """SELECT MAX(CAST(substr(col_j,7,4) AS INTEGER)), COUNT(*)
           FROM materials WHERE substr(col_j,1,6)=? AND length(col_j)=10""",
        (prefix,)).fetchone()
    db_max, total = row[0] or 0, row[1]
    base = db_max if start_seq is None else max(db_max, start_seq)
    codes = []
    for n in range(1, 2):
        seq = base + n
        if seq > 9999:
            raise ValueError(f"类别 {prefix} 流水号已用尽 (>9999)，无法生成")
        codes.append(f"{prefix}{seq:04d}")
    return {
        'prefix': prefix, 'c': c, 'e': e, 'g': g,
        'a': a, 'b': b, 'd': d, 'f': f, 'h': h, 'k': k,
        'ref_name': rname, 'ref_spec': rspec, 'ref_unit': runit,
        'max_seq': base, 'db_max_seq': db_max,
        'db_max_code': (f"{prefix}{db_max:04d}" if db_max else None),
        'total': total, 'codes': codes,
    }
