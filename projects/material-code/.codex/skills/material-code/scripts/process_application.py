# -*- coding: utf-8 -*-
"""处理物料申请表（Excel 第一页）。

流程由 workflow.py 强制控制：读取申请表第一页、只检索在用物料的固定 Top5、执行精确匹配
复用，并仅按已经验证的用户选择写独立结果 Excel。该脚本禁止直接执行。

结果表头使用 A-T 标准结构：
  A=引用(留空表头) B=大类 C=大类编码 D=一类 E=一级子类编码 F=二级 G=二级子类编码
  H=上级分类-用友加 I=旧物料编号 J=新物料编码 K=采购部门 L=图纸编号 M=产品名称
  N=产品规格 O=单位 P=是否要做保质期管理 Q=是否要批次号管理 R=是否要做品牌管理
  S=用途 T=编号申请人
"""
import argparse
import hashlib
import json
import os
import sys
from copy import copy
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from matlib import (connect, DEFAULT_DB, sheet_names, read_sheet,
                    search_top, generate_codes)
from project_paths import find_project_root
from workflow_guard import require_workflow_entry

# 申请表表头 -> 内部字段
HEADERS = {
    'name': '产品名称',
    'spec': '产品规格',
    'unit': '单位',
    'purpose': '用途',
    'applicant': '编号申请人',
    'exp': '是否要做保质期管理',
    'batch': '是否要批次号管理',
    'brand': '是否要做品牌管理',
}

# 结果表头（A 列留空表头）
RESULT_HEADER = [
    '', '大类', '大类编码', '一类', '一级子类编码', '二级', '二级子类编码',
    '上级分类-用友加', '旧物料编号', '新物料编码', '采购部门', '图纸编号',
    '产品名称', '产品规格', '单位', '是否要做保质期管理', '是否要批次号管理',
    '是否要做品牌管理', '用途', '编号申请人',
]
RESULT_TEMPLATE = ''
EXACT_REUSE_FILL_COLOR = 'FFFFC7CE'
EXACT_REUSE_FONT_COLOR = 'FF9C0006'

# 表头文字 -> 输出字段键
_HEADER_FIELD = {
    '大类': 'b', '大类编码': 'c', '一类': 'd', '一级子类编码': 'e', '二级': 'f',
    '二级子类编码': 'g', '上级分类-用友加': 'h', '旧物料编号': '_blank',
    '新物料编码': 'code', '采购部门': 'k', '图纸编号': '_blank',
    '产品名称': 'name', '产品规格': 'spec', '单位': 'unit',
    '是否要做保质期管理': 'exp', '是否要批次号管理': 'batch',
    '是否要做品牌管理': 'brand', '用途': 'purpose', '编号申请人': 'applicant',
}


def read_application(excel_path):
    """读取申请表第一页，返回 (items, meta)。

    items: [{name,spec,unit,purpose,applicant,exp,batch,brand}, ...]
    meta: {sheet, header_row, found, missing, n_items} —— 供 harness 校验/提示用。
    """
    names = sheet_names(excel_path)
    if not names:
        raise ValueError("申请表无工作表")
    sheet = names[0]
    rows = read_sheet(excel_path, sheet)

    header_idx = None
    for idx, row in enumerate(rows):
        texts = [str(c).strip() for c in row]
        if '产品名称' in texts and '用途' in texts:
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("申请表第一页未找到表头（需含'产品名称'和'用途'）")

    header = [str(c).strip() for c in rows[header_idx]]
    keycol = {}
    for key, h in HEADERS.items():
        if h in header:
            keycol[key] = header.index(h)

    items = []
    for row in rows[header_idx + 1:]:
        if not row:
            continue
        get = lambda k: (row[keycol[k]].strip()
                         if k in keycol and keycol[k] < len(row) else '')
        name = get('name')
        if not name:
            continue
        items.append({
            'name': name, 'spec': get('spec'), 'unit': get('unit'),
            'purpose': get('purpose'), 'applicant': get('applicant'),
            'exp': get('exp'), 'batch': get('batch'), 'brand': get('brand'),
        })
    meta = {
        'sheet': sheet,
        'header_row': header_idx + 1,
        'found': [HEADERS[k] for k in keycol],
        'missing': [HEADERS[k] for k in HEADERS if k not in keycol],
        'n_items': len(items),
    }
    return items, meta


def print_app_report(meta, label='申请表'):
    """打印申请表解析报告：工作表/表头行/有效项/已识别列/缺失列。"""
    print(f"[{label}] 工作表={meta['sheet']} 表头行={meta['header_row']} "
          f"有效申请项={meta['n_items']}")
    print(f"  已识别列: {', '.join(meta['found']) or '(无)'}")
    if meta['missing']:
        opt_missing = [m for m in meta['missing'] if m not in ('产品名称', '用途')]
        if opt_missing:
            print(f"  [提示] 未找到列(对应字段将留空): {', '.join(opt_missing)}")


def _row_values(item):
    """把一条结果映射成 RESULT_HEADER 顺序的值列表。"""
    fv = {
        'a': item['a'], 'b': item['b'], 'c': item['c'], 'd': item['d'],
        'e': item['e'], 'f': item['f'], 'g': item['g'], 'h': item['h'],
        'code': item['code'], 'k': item['k'], 'name': item['name'],
        'spec': item['spec'], 'unit': item['unit'], 'exp': item['exp'],
        'batch': item['batch'], 'brand': item['brand'],
        'purpose': item['purpose'], 'applicant': item['applicant'],
    }
    out = []
    for ci, h in enumerate(RESULT_HEADER):
        if ci == 0:
            out.append(fv['a'])
        else:
            key = _HEADER_FIELD.get(h, '_blank')
            out.append(fv.get(key, '') if key != '_blank' else '')
    return out


def resolve_out_path(out_arg, out_dir_arg):
    """解析结果写出路径。

    默认输出到 <工程根>/output 目录，文件名带时间戳后缀
    （申请结果_YYYYMMDD_HHMMSS.xlsx），便于每次使用都留存历史结果。
    - out_arg 为空：自动生成带时间戳的文件到 output 目录
    - out_arg 仅为文件名：自动归入 output 目录
    - out_arg 含目录（完整路径）：以用户指定为准
    """
    root = find_project_root()
    default_dir = root / 'output'
    out_dir = Path(out_dir_arg) if out_dir_arg else default_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_arg:
        p = Path(out_arg)
        if len(p.parts) > 1:
            out_path = p
        else:
            out_path = out_dir / p.name
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = out_dir / f"申请结果_{ts}.xlsx"
    return str(out_path)


def write_result(path, template_path, rows):
    import openpyxl
    header = list(RESULT_HEADER)
    # 优先读取模板表头，保证与用户文件完全一致
    try:
        twb = openpyxl.load_workbook(template_path)
        tws = twb.active
        hdr = [tws.cell(row=1, column=ci + 1).value for ci in range(tws.max_column)]
        if any(str(h).strip() for h in hdr if h is not None):
            header = hdr
    except Exception:
        pass
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(header)
    for r in rows:
        ws.append(_row_values(r))
    wb.save(path)


# ---------------------------------------------------------------------------
# 候选展示 / 生成
# ---------------------------------------------------------------------------

def print_candidates(idx, it, top):
    """打印单条申请项的 Top-N 候选（供交互/审阅）。"""
    meta = (f"规格 {it['spec'] or '-'} | 单位 {it['unit'] or '-'} | "
            f"用途 {it['purpose'] or '-'} | 申请人 {it['applicant'] or '-'}")
    print(f"=== 申请项 {idx}: {it['name']} （{meta}）===")
    if not top:
        print("  [无匹配候选] 库内未找到相近物料，请确认类别或手动指定 C/E/G。\n")
        return
    for n, (s, r) in enumerate(top, 1):
        (rid, xrow, a, b, c, d, e, f, g, h, i, j, k,
         name, spec, unit, usage, retired) = r
        flag = ' [已停用]' if retired else ''
        print(f"  [{n}] 编码={j}{flag}  分类: {b}({c}) > {d}({e}) > {f}({g})  H={h}")
        print(f"       名称:{name or '-'} | 规格:{spec or '-'} | 单位:{unit or '-'} | 相似度:{s:.3f}")
    print()


# ---------------------------------------------------------------------------
# wizard 模式（预查候选缓存 + 边选边写结果，正式 SQL 始终只是 Excel 镜像）
# ---------------------------------------------------------------------------

def _cached_exact_row(r):
    """把 materials 的 SELECT * 行压缩成候选缓存中的精确匹配记录。"""
    return {
        'id': r[0], 'a': r[2], 'b': r[3], 'c': r[4],
        'd': r[5], 'e': r[6], 'f': r[7], 'g': r[8],
        'h': r[9], 'code': r[11], 'k': r[12],
        'name': r[13], 'spec': r[14], 'unit': r[15],
    }


def find_exact_active_match(con, item):
    """按去首尾空格后的「产品名称 + 产品规格」查找在用物料的严格匹配。

    返回 (exact, conflicts)：
    - 只有一个不同编码时 exact 为可自动复用的记录；
    - 存在多个不同编码时 conflicts 列出全部记录，不自动选择；
    - 无匹配时两者均为空。
    """
    name = str(item.get('name', '') or '').strip()
    spec = str(item.get('spec', '') or '').strip()
    rows = con.execute(
        "SELECT * FROM materials WHERE retired=0 AND trim(name)=? "
        "AND trim(spec)=? AND col_j<>'' ORDER BY col_j,id",
        (name, spec)).fetchall()
    if not rows:
        return None, []
    by_code = {}
    for row in rows:
        by_code.setdefault(row[11], row)
    records = [_cached_exact_row(by_code[code]) for code in sorted(by_code)]
    if len(records) == 1:
        return records[0], []
    return None, records

def precompute_cache(excel, db, cache_path):
    """一次性预查全部申请项的 Top-N 候选并缓存到 JSON。

    之后每轮交互只读缓存（毫秒级），不再对 5100 行重跑模糊检索。
    缓存结构: {"_meta": {db_count, db_fp, precomputed_at, excel, top},
              "items": [{idx,name,spec,unit,purpose,applicant,exact_match,
                         exact_conflicts,
                         candidates:[{n,id,code,b,c,d,e,f,g,h,name,spec,unit,sim}, ...]}]}
    _meta 用于后续校验缓存是否仍与当前数据库一致（防 re-sync 后候选 id 错位）。
    """
    items, meta = read_application(excel)
    print_app_report(meta, '预查申请表')
    con = connect(db)
    cnt, fp = db_fingerprint(con)
    data = []
    for idx, it in enumerate(items, 1):
        exact, conflicts = find_exact_active_match(con, it)
        q = f"{it['name']} {it['purpose']}".strip()
        top5 = search_top(con, q)
        cands = []
        for n, (s, r) in enumerate(top5, 1):
            # r 顺序见 matlib.SELECT_ALL: col_b=r[3],col_c=r[4],col_d=r[5],
            #   col_e=r[6],col_f=r[7],col_g=r[8],col_h=r[9],col_j=r[11]
            cands.append({
                'n': n, 'id': r[0], 'code': r[11],
                'b': r[3], 'c': r[4], 'd': r[5], 'e': r[6],
                'f': r[7], 'g': r[8], 'h': r[9],
                'name': r[13], 'spec': r[14], 'unit': r[15],
                'sim': round(s, 3),
            })
        if conflicts:
            conflict_ids = {row['id'] for row in conflicts}
            exact_cands = [{
                'n': n, 'id': row['id'], 'code': row['code'],
                'b': row['b'], 'c': row['c'], 'd': row['d'], 'e': row['e'],
                'f': row['f'], 'g': row['g'], 'h': row['h'],
                'name': row['name'], 'spec': row['spec'], 'unit': row['unit'],
                'sim': 1.0,
            } for n, row in enumerate(conflicts, 1)]
            rest = [cand for cand in cands if cand['id'] not in conflict_ids]
            cands = exact_cands + rest[:max(0, 5 - len(exact_cands))]
            for n, cand in enumerate(cands, 1):
                cand['n'] = n
        cached_item = {
            'idx': idx, 'name': it['name'], 'spec': it['spec'],
            'unit': it['unit'], 'purpose': it['purpose'],
            'applicant': it['applicant'], 'candidates': cands,
        }
        if exact:
            cached_item['exact_match'] = exact
        if conflicts:
            cached_item['exact_conflicts'] = conflicts
        data.append(cached_item)
    con.close()
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        '_meta': {
            'db_count': cnt, 'db_fp': fp,
            'precomputed_at': datetime.now().isoformat(timespec='seconds'),
            'excel': excel, 'top': 5,
        },
        'items': data,
    }
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[已预查并缓存] {len(data)} 条申请项的 Top5 候选 -> {cache_path}")
    print(f"  数据库指纹: 行数={cnt} fp={fp}")
    print("  之后用 --show <项号> 毫秒级展示；--commit <项号>:<候选> 生成预定编码并写结果。")


def load_cache(cache_path):
    if not os.path.exists(cache_path):
        raise FileNotFoundError(
            f"缓存不存在: {cache_path}\n请先运行 --precompute 预查候选。")
    with open(cache_path, encoding='utf-8') as f:
        obj = json.load(f)
    # 兼容旧版（裸 list）与新版（{_meta, items}）
    if isinstance(obj, dict) and 'items' in obj:
        return obj['items']
    return obj


def db_fingerprint(con):
    """返回 (count, fp)。fp = 全部 col_j 升序拼接后 md5 前 12 位。

    内容不变则 fp 不变（仅重 sync 同一张 Excel 不会误判陈旧）；
    行数或任一编码变化则 fp 变化 → 触发缓存陈旧。
    """
    cnt = con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "SELECT col_j FROM materials ORDER BY col_j")]
    fp = hashlib.md5('\n'.join(codes).encode('utf-8')).hexdigest()[:12]
    return cnt, fp


def cache_sample_matches(con, cache_path):
    """抽样核对缓存候选的 id 是否仍对应同一 col_j（检测 re-sync 后 id 错位）。

    检查前若干项的首个候选；全部匹配视为缓存有效。返回 (ok, detail)。
    正常 --commit 只是追加新行、不改动既有 id，故不会误判陈旧；
    只有 sync_db.py 的 DELETE+INSERT 重新分配 rowid 才会触发。
    """
    try:
        items = load_cache(cache_path)
    except FileNotFoundError:
        return False, '缓存不存在'
    checks = 0
    for it in items:
        exact = it.get('exact_match')
        if exact:
            row = con.execute(
                "SELECT col_j,name,spec,retired FROM materials WHERE id=?",
                (exact['id'],)).fetchone()
            if row is None or row[0] != exact['code'] or row[3] != 0:
                return False, (f"精确匹配 id={exact['id']} 已变化，"
                               "可能 re-sync 过，请重新 --precompute")
            checks += 1
            if checks >= 3:
                break
            continue
        cands = it.get('candidates') or []
        if not cands:
            continue
        c = cands[0]
        row = con.execute(
            "SELECT col_j FROM materials WHERE id=?", (c['id'],)).fetchone()
        if row is None:
            return False, f"候选 id={c['id']} 在库中已不存在(可能 re-sync 过)"
        if row[0] != c['code']:
            return False, (f"候选 id={c['id']} 编码已变(缓存 {c['code']} "
                           f"-> 库 {row[0]})，可能 re-sync 过，请重新 --precompute")
        checks += 1
        if checks >= 3:
            break
    if checks == 0:
        return False, '缓存中无可核对候选'
    return True, ''


def show_item_md(con, cache_path, idx):
    """从缓存读取第 idx 项并输出 Markdown 候选表格（含该类别当前最大编码，供预判）。"""
    data = load_cache(cache_path)
    if idx < 1 or idx > len(data):
        print(f"[错误] 项 {idx} 超出范围(共 {len(data)} 条)")
        return
    it = data[idx - 1]
    meta = (f"规格 {it['spec'] or '-'} | 单位 {it['unit'] or '-'} | "
            f"用途 {it['purpose'] or '-'} | 申请人 {it['applicant'] or '-'}")
    print(f"**申请项 {idx} — {it['name']}**")
    print(f"{meta}\n")
    exact = it.get('exact_match')
    if exact:
        print(f"> [完全一致] 产品名称和产品规格与总表编码 `{exact['code']}` 完全一致。")
        print("> 此项不生成新编码；结果 Excel 直接复用原编码并自动进入下一项。")
        return
    conflicts = it.get('exact_conflicts') or []
    if conflicts:
        codes = '、'.join(f"`{row['code']}`" for row in conflicts)
        print(f"> [完全一致·冲突] 产品名称和产品规格对应多个编码：{codes}。")
        print("> 为避免误用，本项不自动复用，请人工确认下面的候选。\n")
    print("| # | 参考编码 | 三级类别 | 参考名称/规格 | 相似度 | 该类别当前最大编码 |")
    print("|---|---|---|---|---|---|")
    for c in it['candidates']:
        cls = f"{c['b']}({c['c']})>{c['d']}({c['e']})>{c['f']}({c['g']}) H={c['h']}"
        ns = f"{c['name']} / {c['spec']}" if c['spec'] else (c['name'] or '-')
        row = con.execute(
            "SELECT MAX(CAST(substr(col_j,7,4) AS INTEGER)) FROM materials "
            "WHERE substr(col_j,1,6)=? AND length(col_j)=10", (c['h'],)).fetchone()
        mx = row[0] or 0
        max_code = f"{c['h']}{mx:04d}" if mx else "(该类别暂无编码)"
        print(f"| {c['n']} | {c['code']} | {cls} | {ns} | {c['sim']:.3f} | {max_code} |")
    print()
    print("> 选 `[0]` = 不确定·留空（结果行仅保留原始申请信息，不生成编码、不落库）。")


def _highlight_exact_reuse_row(ws, row_index):
    """用浅红底、深红字标记整行“完全一致复用”的结果。"""
    from openpyxl.styles import PatternFill

    fill = PatternFill(fill_type='solid', fgColor=EXACT_REUSE_FILL_COLOR)
    for cell in ws[row_index]:
        cell.fill = fill
        font = copy(cell.font)
        font.color = EXACT_REUSE_FONT_COLOR
        cell.font = font


def append_result_row(path, template_path, row_dict, *, exact_reuse=False):
    """把一行结果增量追加到结果 Excel（文件不存在则新建表头）。

    exact_reuse 为真时，整行标为浅红色，便于人工核对复用旧编码的记录。
    """
    import openpyxl
    header = list(RESULT_HEADER)
    try:
        twb = openpyxl.load_workbook(template_path)
        tws = twb.active
        hdr = [tws.cell(row=1, column=ci + 1).value for ci in range(tws.max_column)]
        if any(str(h).strip() for h in hdr if h is not None):
            header = hdr
    except Exception:
        pass
    if os.path.exists(path):
        wb = openpyxl.load_workbook(path)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Sheet1'
        ws.append(header)
    ws.append(_row_values(row_dict))
    if exact_reuse:
        _highlight_exact_reuse_row(ws, ws.max_row)
    wb.save(path)
    return path


def reuse_exact_item(con, items, cache, idx, result_path, template,
                     exact_record=None):
    """把唯一精确匹配的原编码写入结果表，不生成编码、不写数据库。"""
    if idx < 1 or idx > len(items) or idx > len(cache):
        raise ValueError(f"项 {idx} 超出范围")
    it = items[idx - 1]
    exact = exact_record or cache[idx - 1].get('exact_match')
    if not exact:
        raise ValueError(f"第 {idx} 项没有唯一精确匹配")
    row = con.execute(
        "SELECT * FROM materials WHERE id=? AND retired=0", (exact['id'],)).fetchone()
    if row is None or row[11] != exact['code']:
        raise ValueError(
            f"精确匹配缓存陈旧: id={exact['id']} / 编码={exact['code']}，"
            "请重新 --precompute")
    if str(row[13] or '').strip() != str(it['name'] or '').strip() or \
            str(row[14] or '').strip() != str(it['spec'] or '').strip():
        raise ValueError(f"第 {idx} 项的名称/规格已不再与缓存参考行完全一致")
    row_dict = {
        'a': row[2] or f"{row[3]}{row[5]}{row[7]}",
        'b': row[3], 'c': row[4], 'd': row[5], 'e': row[6],
        'f': row[7], 'g': row[8], 'h': row[9], 'code': row[11],
        'k': row[12] or '',
        'name': it['name'], 'spec': it['spec'], 'unit': it['unit'],
        'exp': it['exp'], 'batch': it['batch'], 'brand': it['brand'],
        'purpose': it['purpose'], 'applicant': it['applicant'],
    }
    append_result_row(result_path, template, row_dict, exact_reuse=True)
    return row[11], exact, it


def reuse_all_exact(con, items, cache, result_path, template):
    """将缓存中的唯一精确匹配逐行写入结果，返回 (复用项号, 冲突项号)。"""
    reused, conflicts = [], []
    for idx, cached in enumerate(cache, 1):
        conflict_rows = cached.get('exact_conflicts') or []
        if conflict_rows:
            codes = '、'.join(row['code'] for row in conflict_rows)
            print(f"[完全一致·冲突] 第{idx}项 「{cached['name']}」 / "
                  f"「{cached['spec'] or '(空规格)'}」对应多个在用编码: {codes}")
            print("   不自动复用；保留给人工候选确认。")
            conflicts.append(idx)
            continue
        if not cached.get('exact_match'):
            continue
        code, exact, it = reuse_exact_item(
            con, items, cache, idx, result_path, template)
        print(f"[完全一致·复用] 第{idx}项 「{it['name']}」 / "
              f"「{it['spec'] or '(空规格)'}」与总表编码 {code} 完全一致")
        print("   不生成新编码、不写数据库；结果 Excel 已直接复用原编码。")
        reused.append(idx)
    print(f"[精确匹配完成] 自动复用 {len(reused)} 项；多编码冲突 {len(conflicts)} 项。")
    print(f"[结果文件] {result_path}")
    return reused, conflicts


def _result_prefix_max(result_path, prefix):
    """读取当前 job 结果表，返回该 6 位类别前缀已预定的最大流水号。"""
    import openpyxl
    if not os.path.exists(result_path):
        return 0
    try:
        wb = openpyxl.load_workbook(result_path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = [str(v or '').strip() for v in next(rows)]
        if '新物料编码' not in header:
            wb.close()
            return 0
        code_i = header.index('新物料编码')
        maximum = 0
        for row in rows:
            if code_i >= len(row):
                continue
            code = str(row[code_i] or '').strip()
            if len(code) == 10 and code.isdigit() and code.startswith(prefix):
                maximum = max(maximum, int(code[6:]))
        wb.close()
        return maximum
    except Exception:
        return 0


def commit_item(con, items, cache, idx, pick, result_path, template):
    """按缓存中第 idx 项第 pick 候选生成预定编码并写结果表。

    正式 SQL 不写入任何申请结果；续号基准取 max(SQL 正式最大号, 当前 job 已预定最大号)。
    """
    if idx < 1 or idx > len(items):
        raise ValueError(f"项 {idx} 超出范围")
    it = items[idx - 1]
    if idx <= len(cache) and cache[idx - 1].get('exact_match'):
        code, exact, it = reuse_exact_item(
            con, items, cache, idx, result_path, template)
        return code, {'reused_exact': True, 'exact': exact}, it
    # 留空选项：不生成编码、不落库，结果行仅保留原始申请信息
    if pick in (0, 'blank', '不确定', '空'):
        row_dict = {
            'a': '', 'b': '', 'c': '', 'd': '', 'e': '', 'f': '',
            'g': '', 'h': '', 'code': '', 'k': '',
            'name': it['name'], 'spec': it['spec'], 'unit': it['unit'],
            'exp': it['exp'], 'batch': it['batch'], 'brand': it['brand'],
            'purpose': it['purpose'], 'applicant': it['applicant'],
        }
        append_result_row(result_path, template, row_dict)
        return None, None, it
    if idx > len(cache) or not cache[idx - 1]['candidates']:
        raise ValueError(f"第 {idx} 项无候选")
    cands = cache[idx - 1]['candidates']
    if pick < 1 or pick > len(cands):
        raise ValueError(f"候选序号 {pick} 无效(共 {len(cands)} 条)")
    cand = cands[pick - 1]
    ref = con.execute("SELECT * FROM materials WHERE id=?", (cand['id'],)).fetchone()
    if ref is None:
        raise ValueError(f"参考行 id={cand['id']} 不存在")
    # 安全网：核对缓存里该候选的编码与库中实际一致；re-sync 重排 rowid 会被此处拦下
    if ref[11] != cand['code']:
        raise ValueError(
            f"缓存陈旧: 候选 id={cand['id']} 编码已变(缓存 {cand['code']} "
            f"-> 库 {ref[11]})，请重新 --precompute 后再提交")
    conflicts = cache[idx - 1].get('exact_conflicts') or []
    if conflicts:
        by_code = {row['code']: row for row in conflicts}
        if cand['code'] not in by_code:
            codes = '、'.join(sorted(by_code))
            raise ValueError(
                f"名称+规格已与总表多个编码完全一致({codes})；"
                "请选择其中一个精确匹配编码，不得生成新编码")
        code, exact, it = reuse_exact_item(
            con, items, cache, idx, result_path, template,
            exact_record=by_code[cand['code']])
        return code, {'reused_exact': True, 'exact': exact,
                      'resolved_conflict': True}, it
    prefix = f"{ref[4]}{ref[6]}{ref[8]}"
    reserved_max = _result_prefix_max(result_path, prefix)
    res = generate_codes(con, ref, start_seq=reserved_max)
    new_code = res['codes'][0]
    row_dict = {
        'a': f"{res['b']}{res['d']}{res['f']}",
        'b': res['b'], 'c': res['c'], 'd': res['d'], 'e': res['e'],
        'f': res['f'], 'g': res['g'], 'h': res['prefix'], 'code': new_code,
        'k': res['k'] or '',
        'name': it['name'], 'spec': it['spec'], 'unit': it['unit'],
        'exp': it['exp'], 'batch': it['batch'], 'brand': it['brand'],
        'purpose': it['purpose'], 'applicant': it['applicant'],
    }
    append_result_row(result_path, template, row_dict)  # 在输出结果里写一行
    return new_code, res, it


def _committed_keys(result_path):
    """读取结果表，返回已处理项的标识集合，用于 auto-threshold 跳过重复项。

    位置按结果表表头动态定位（产品名称/产品规格/用途/编号申请人）。
    """
    import openpyxl
    keys = set()
    if not os.path.exists(result_path):
        return keys
    try:
        wb = openpyxl.load_workbook(result_path, read_only=True)
        ws = wb.active
        hdr = [str(c).strip() if c is not None else ''
               for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        def _col(*names):
            for n in names:
                if n in hdr:
                    return hdr.index(n)
            return -1
        name_i = _col('产品名称', '名称')
        spec_i = _col('产品规格', '规格')
        purp_i = _col('用途')
        appl_i = _col('编号申请人', '申请人')
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or len(row) <= name_i:
                continue
            nm = str(row[name_i] or '').strip()
            if not nm:
                continue
            sp = (str(row[spec_i] or '').strip()
                  if 0 <= spec_i < len(row) else '')
            pu = (str(row[purp_i] or '').strip()
                  if 0 <= purp_i < len(row) else '')
            ap = (str(row[appl_i] or '').strip()
                  if 0 <= appl_i < len(row) else '')
            keys.add("|".join([nm, sp, pu, ap]))
    except Exception:
        pass
    return keys


def auto_threshold(con, items, cache, threshold, result_path, template):
    """置信度阈值自动选用。

    对相似度 ≥ threshold 的申请项，直接选用**最大相似度的候选（Top1）**生成预定编码，
    不再等待人工选择；低于阈值的项收集起来交用户交互。已处理项自动跳过。

    每项生成时打印「正式库原最大编码 + 新预定编码 + 组合」，保证结果可核对。
    """
    done = _committed_keys(result_path)
    auto_count = 0
    reused_count = 0
    remain = []
    for idx, it in enumerate(items, 1):
        key = "|".join(str(it.get(k, '') or '').strip()
                       for k in ('name', 'spec', 'purpose', 'applicant'))
        if key in done:
            continue
        cached = cache[idx - 1] if idx <= len(cache) else {}
        if cached.get('exact_match'):
            try:
                code, res, itm = commit_item(
                    con, items, cache, idx, 1, result_path, template)
            except Exception as e:
                print(f"[跳过] 第{idx}项精确匹配复用失败: {e}")
                remain.append(idx)
                continue
            print(f"[完全一致·复用] 第{idx}项 「{it['name']}」 / "
                  f"「{it['spec'] or '(空规格)'}」与总表编码 {code} 完全一致")
            print("   不生成新编码、不写数据库；结果 Excel 已直接复用原编码。")
            reused_count += 1
            done.add(key)
            continue
        if cached.get('exact_conflicts'):
            remain.append(idx)
            continue
        cands = cache[idx - 1]['candidates'] if idx <= len(cache) else []
        if not cands:
            remain.append(idx)
            continue
        top = cands[0]  # 最大相似度候选（检索已按相似度降序）
        if top['sim'] >= threshold:
            try:
                new_code, res, itm = commit_item(
                    con, items, cache, idx, 1, result_path, template)
            except Exception as e:
                print(f"[跳过] 第{idx}项: {e}")
                remain.append(idx)
                continue
            seq = res['max_seq'] + 1
            dbmax = res['db_max_code'] or "(该类别暂无编码)"
            print(f"[自动选用] 第{idx}项 「{it['name']}」 "
                  f"相似度 {top['sim']:.3f} ≥ {threshold} "
                  f"→ {new_code} (H={res['prefix']}, 参考 {res['b']}>{res['d']}>{res['f']})")
            print(f"   类别 C/E/G = {res['c']}/{res['e']}/{res['g']} (H={res['prefix']})")
            print(f"   表内该类别原最新编码: {dbmax}  (流水号 {res['db_max_seq']:04d})")
            if res['max_seq'] > res['db_max_seq']:
                print(f"   当前 job 已预定至流水号: {res['max_seq']:04d}")
            print(f"   本次生成: {new_code} = {res['prefix']} + {seq:04d}  "
                  f"(流水号 {res['max_seq']}+1)")
            print(f"   组合: 前缀{res['c']}{res['e']}{res['g']} + 流水号{seq:04d}")
            auto_count += 1
        else:
            remain.append(idx)
    print()
    print(f"[精确匹配复用] 共 {reused_count} 项直接使用总表已有编码。")
    print(f"[自动选用完成] 共自动生成预定编码 {auto_count} 项（相似度 ≥ {threshold}）。")
    if remain:
        print(f"[待人工核对] 以下 {len(remain)} 项相似度 < {threshold}，"
              f"请用 --show N 查看后 --commit N:pick（或 N:0 留空）:")
        print("  " + ", ".join(str(i) for i in remain))
    else:
        print("[待人工核对] 无，全部申请项已自动选用。")
    print(f"[结果文件] {result_path}")


def generate_and_write(con, items, per_item, picks, args):
    """按 picks(申请项序号->候选序号) 生成新物料编码并写出结果。"""
    print('=' * 72)
    print("按用户选择生成新物料编码:\n")
    rows_out = []
    next_seq = {}  # 同一 C/E/G 序列在本次运行内连续分配，避免重复流水号
    for item_idx in sorted(picks):
        cand_idx = picks[item_idx]
        if item_idx < 1 or item_idx > len(items):
            print(f"[跳过] 第{item_idx}项超出范围")
            continue
        it = items[item_idx - 1]
        # 留空选项：不生成编码、不落库，仅保留原始申请信息
        if cand_idx == 0:
            print(f"申请项 {item_idx} 「{it['name']}」→ 留空（不生成编码、不落库）")
            rows_out.append({
                'a': '', 'b': '', 'c': '', 'd': '', 'e': '', 'f': '',
                'g': '', 'h': '', 'code': '', 'k': '',
                'name': it['name'], 'spec': it['spec'], 'unit': it['unit'],
                'exp': it['exp'], 'batch': it['batch'], 'brand': it['brand'],
                'purpose': it['purpose'], 'applicant': it['applicant'],
            })
            continue
        top = per_item[item_idx - 1]
        if not top or cand_idx < 1 or cand_idx > len(top):
            print(f"[跳过] 第{item_idx}项候选序号 {cand_idx} 无效")
            continue
        s, r = top[cand_idx - 1]
        rid = r[0]
        ref = con.execute("SELECT * FROM materials WHERE id=?", (rid,)).fetchone()
        prefix = f"{ref[4]}{ref[6]}{ref[8]}"  # c+e+g
        try:
            res = generate_codes(con, ref, args.count,
                                 start_seq=next_seq.get(prefix))
        except ValueError as e:
            print(f"[跳过] 第{item_idx}项: {e}")
            continue
        next_seq[prefix] = res['max_seq'] + args.count
        a_val = f"{res['b']}{res['d']}{res['f']}"
        print(f"申请项 {item_idx} 「{it['name']}」→ 参考 "
              f"{res['b']}({res['c']})>{res['d']}({res['e']})>{res['f']}({res['g']}) H={res['prefix']}")
        print(f"  最高流水 {res['max_seq']:04d} → 新编码: {' / '.join(res['codes'])}")
        for nj in res['codes']:
            print(f"  A-T行: " + ' | '.join([
                a_val, res['b'], res['c'], res['d'], res['e'], res['f'],
                res['g'], res['prefix'], '', nj, res['k'] or '', '',
                it['name'], it['spec'], it['unit'], it['exp'], it['batch'],
                it['brand'], it['purpose'], it['applicant']]))
        print(f"  （P/Q/R 保质期/批次/品牌管理沿用申请表填写值）\n")
        for nj in res['codes']:
            rows_out.append({
                'a': a_val, 'b': res['b'], 'c': res['c'], 'd': res['d'],
                'e': res['e'], 'f': res['f'], 'g': res['g'], 'h': res['prefix'],
                'code': nj, 'k': res['k'] or '', 'name': it['name'],
                'spec': it['spec'], 'unit': it['unit'], 'exp': it['exp'],
                'batch': it['batch'], 'brand': it['brand'],
                'purpose': it['purpose'], 'applicant': it['applicant'],
            })

    if rows_out:
        out_path = resolve_out_path(args.out, args.out_dir)
        write_result(out_path, args.template, rows_out)
        print(f"[已写出结果] {out_path}（表头对齐 {args.template}）")
    else:
        print("[无输出] 未生成任何编码。")


def run_interactive(con, items, per_item):
    """终端逐项交互：每条列出 Top5，由用户选择参考项。返回 picks 字典。"""
    picks = {}
    n = len(items)
    for idx, (it, top) in enumerate(zip(items, per_item), 1):
        print_candidates(idx, it, top)
        if not top:
            ans = input("  回车跳过此项: ").strip()
            continue
        while True:
            ans = input(f"  选择参考项 [1-{len(top)}] "
                        f"(回车=第1项, 0/blank=留空, s 跳过, q 结束并生成已选项): ").strip()
            if ans.lower() == 'q':
                return picks
            if ans.lower() == 's':
                break
            if ans.lower() in ('0', 'blank', '不确定', '空'):
                picks[idx] = 0
                break
            ci = 1 if ans == '' else None
            if ci is None:
                try:
                    ci = int(ans)
                except ValueError:
                    ci = None
            if ci is not None and 1 <= ci <= len(top):
                picks[idx] = ci
                break
            print("  输入无效，请重输（1-N / 0=留空 / s / q）")
    return picks


def write_review_template(path, items, per_item):
    """导出候选对照模板：每行一条候选，含“选定(填1-5)”空列供用户填写。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '候选对照'
    headers = ["申请项", "产品名称", "用途", "规格", "单位", "申请人", "候选序",
               "推荐", "编码", "大类", "一级子类", "二级子类", "H", "候选名称",
               "候选规格", "候选单位", "相似度", "备注", "选定(填1-5)"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(horizontal="center")
    rec_fill = PatternFill("solid", fgColor="FFF2CC")
    for idx, (it, top) in enumerate(zip(items, per_item), 1):
        if not top:
            ws.append([idx, it['name'], it['purpose'], it['spec'], it['unit'],
                       it['applicant'], "-", "", "无候选", "", "", "", "", "", "",
                       "", "", "库内无在用物料候选，请核对分类依据", ""])
            continue
        for m_, (s, r) in enumerate(top, 1):
            rec = "★推荐" if m_ == 1 else ""
            note = "已停用" if r[17] else ""
            ws.append([idx, it['name'], it['purpose'], it['spec'], it['unit'],
                       it['applicant'], m_, rec, r[11], r[3], r[5], r[7], r[9],
                       r[13], r[14], r[15], round(s, 3), note, ""])
            if m_ == 1:
                for cell in ws[ws.max_row]:
                    cell.fill = rec_fill
    ws.freeze_panes = "A2"
    for col in ws.columns:
        vals = [len(str(c.value)) for c in col if c.value is not None]
        w = max(vals) if vals else 8
        ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 8), 42)
    wb.save(path)
    print(f"[已写出候选对照模板] {path}")
    print("  在“选定(填1-5)”列填写每项参考的候选序号，"
          "保存后用 --apply-review 读回并生成结果。")


def read_review_picks(path, n_items):
    """读回已填写的候选对照模板，返回 picks 字典（申请项序号->候选序号）。"""
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    picks = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        try:
            idx = int(row[0])
        except (ValueError, TypeError):
            continue
        sel = row[18] if len(row) > 18 else None
        if sel in (None, ''):
            continue
        try:
            ci = int(str(sel).strip())
        except ValueError:
            continue
        if 1 <= ci <= 5:
            picks[idx] = ci
    return picks


def parse_pick_arg(pick_str, n_items):
    picks = {}
    for pair in pick_str.replace('，', ',').split(','):
        pair = pair.strip()
        if not pair or ':' not in pair:
            continue
        i_s, c_s = pair.split(':', 1)
        try:
            picks[int(i_s)] = int(c_s)
        except ValueError:
            print(f"[警告] 忽略无效选择: {pair}")
    return picks


def main():
    require_workflow_entry()
    ap = argparse.ArgumentParser()
    ap.add_argument('excel', help='申请表 Excel 路径（第一页含申请信息）')
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--template', default=RESULT_TEMPLATE,
                    help='可选结果表头模板')
    # ---- wizard 模式（预查缓存 + 边选边写独立结果）----
    ap.add_argument('--precompute', action='store_true',
                    help='一次性预查全部申请项的 Top-N 候选并缓存（不交互）')
    ap.add_argument('--reuse-exact', action='store_true',
                    help='把名称+规格唯一完全一致的在用物料直接复用到结果表（不写数据库）')
    ap.add_argument('--cache', default='',
                    help='候选缓存路径（默认 <output>/_candidates.json）')
    ap.add_argument('--show', type=int, default=0,
                    help='从缓存读取并展示第 N 项候选（毫秒级，不重检索）')
    ap.add_argument('--commit', nargs='*', default=[],
                    help='提交选择并生成预定编码，格式 "idx:pick"（可多个），如 --commit 20:1 21:2')
    ap.add_argument('--auto-threshold', type=float, default=None,
                    help='置信度阈值：相似度≥该值的申请项自动选用「最大相似度候选」'
                         '（Top1）生成预定编码，低于阈值的留给人工核对'
                         '（如 --auto-threshold 0.7）。缺失缓存时自动预查一次。')
    ap.add_argument('--result', default='',
                    help='wizard 结果写出路径（默认 <output>/申请结果_wizard.xlsx）')
    args = ap.parse_args()

    root = find_project_root()
    cache_path = args.cache or str(root / 'output' / '_candidates.json')
    result_path = args.result or str(root / 'output' / '申请结果_wizard.xlsx')

    # ---- 预查候选（一次性重计算，之后交互免重检索）----
    if args.precompute:
        precompute_cache(args.excel, args.db, cache_path)
        return

    # ---- 自动复用名称+规格唯一完全一致的在用物料（只写 job 结果，不写 DB）----
    if args.reuse_exact:
        con = connect(args.db)
        if not con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]:
            print("[错误] 物料库为空，请先运行 sync_db.py 同步 Excel。")
            sys.exit(1)
        ok, reason = cache_sample_matches(con, cache_path)
        if not ok:
            print(f"[错误] 候选缓存可能陈旧: {reason}")
            print("  精确匹配复用前必须重新 --precompute。")
            con.close()
            sys.exit(1)
        items, _meta = read_application(args.excel)
        cache = load_cache(cache_path)
        reuse_all_exact(con, items, cache, result_path, args.template)
        con.close()
        return

    # ---- 展示单项（读缓存，毫秒级；连库查该类别当前最大编码）----
    if args.show:
        con = connect(args.db)
        if not con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]:
            print("[错误] 物料库为空，请先运行 sync_db.py 同步 Excel。")
            sys.exit(1)
        ok, reason = cache_sample_matches(con, cache_path)
        if not ok:
            print(f"[错误] 候选缓存已经失效: {reason}")
            print("  必须由 workflow.py 重新 prepare，禁止展示陈旧候选。")
            con.close()
            sys.exit(1)
        show_item_md(con, cache_path, args.show)
        con.close()
        return

    # ---- 置信度阈值自动选用（相似度≥阈值自动选 Top1 生成预定编码）----
    if args.auto_threshold is not None:
        if not (0 < args.auto_threshold <= 1):
            print("[错误] --auto-threshold 需在 (0,1] 范围，如 0.7。")
            sys.exit(1)
        con = connect(args.db)
        if not con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]:
            print("[错误] 物料库为空，请先运行 sync_db.py 同步 Excel。")
            sys.exit(1)
        # 缓存缺失或与当前库不一致(可能 re-sync 后 id 错位) -> 自动重新预查
        ok, reason = cache_sample_matches(con, cache_path)
        if not ok:
            if os.path.exists(cache_path):
                print(f"[提示] 候选缓存可能陈旧: {reason}，自动重新预查...")
            else:
                print("[提示] 候选缓存不存在，自动预查一次...")
            con.close()
            precompute_cache(args.excel, args.db, cache_path)
            con = connect(args.db)
        items, _meta = read_application(args.excel)
        cache = load_cache(cache_path)
        auto_threshold(con, items, cache, args.auto_threshold,
                       result_path, args.template)
        con.close()
        return

    # ---- 提交选择：边选边生成预定编码 + 写结果表（不写正式 SQL）----
    if args.commit:
        con = connect(args.db)
        if not con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]:
            print("[错误] 物料库为空，请先运行 sync_db.py 同步 Excel。")
            sys.exit(1)
        # 缓存必须与当前库一致，否则候选 id 可能错位 → 落错类别
        ok, reason = cache_sample_matches(con, cache_path)
        if not ok:
            print(f"[错误] 候选缓存可能陈旧: {reason}")
            print("  提交前必须重新 --precompute，否则可能按错误候选生成编码。"
                  "（--show 时也会有此提示）")
            sys.exit(1)
        items, _meta = read_application(args.excel)
        print_app_report(_meta, '提交申请表')
        cache = load_cache(cache_path)
        picks = {}
        for spec_ in args.commit:
            if ':' not in spec_:
                print(f"[警告] 忽略无效 --commit {spec_}")
                continue
            i_s, c_s = spec_.split(':', 1)
            c_s = c_s.strip().lower()
            try:
                if c_s in ('0', 'blank', '不确定', '空'):
                    picks[int(i_s)] = 0
                else:
                    picks[int(i_s)] = int(c_s)
            except ValueError:
                print(f"[警告] 忽略无效 --commit {spec_}")
        if not picks:
            print("[无选择] 未提交任何项。")
            con.close()
            return
        # 幂等：结果表里已处理的项(名称/规格/用途/申请人)不重复生成
        done = _committed_keys(result_path)
        for idx in sorted(picks):
            it = items[idx - 1] if 1 <= idx <= len(items) else None
            if it is None:
                print(f"[跳过] 第{idx}项 超出范围")
                continue
            if picks[idx] != 0:
                key = "|".join(str(it.get(k, '') or '').strip()
                               for k in ('name', 'spec', 'purpose', 'applicant'))
                if key in done:
                    print(f"[跳过] 第{idx}项 「{it['name']}」 已处理(结果表中已存在)，"
                          f"不重复生成；如需重做请先从结果表删除该行。")
                    continue
            try:
                new_code, res, it = commit_item(
                    con, items, cache, idx, picks[idx], result_path, args.template)
            except Exception as e:
                print(f"[跳过] 第{idx}项: {e}")
                continue
            if new_code is not None:
                done.add("|".join(str(it.get(k, '') or '').strip()
                                   for k in ('name', 'spec', 'purpose', 'applicant')))
            if new_code is None:
                print(f"已留空 第{idx}项 「{it['name']}」 "
                      f"(不生成编码、不落库；结果行仅保留原始申请信息)")
                continue
            if res.get('reused_exact'):
                exact = res['exact']
                print(f"[完全一致·复用] 第{idx}项 「{it['name']}」 / "
                      f"「{it['spec'] or '(空规格)'}」与总表编码 {new_code} 完全一致")
                print("   不生成新编码、不写数据库；结果 Excel 已直接复用原编码。")
                continue
            seq = res['max_seq'] + 1
            dbmax = res['db_max_code'] or "(该类别暂无编码)"
            print(f"已生成预定编码+结果表 第{idx}项 「{it['name']}」 → {new_code} "
                  f"(H={res['prefix']}, 参考 {res['b']}>{res['d']}>{res['f']})")
            print(f"   类别 C/E/G = {res['c']}/{res['e']}/{res['g']} (H={res['prefix']})")
            print(f"   表内该类别原最新编码: {dbmax}  (流水号 {res['db_max_seq']:04d})")
            if res['max_seq'] > res['db_max_seq']:
                print(f"   当前 job 已预定至流水号: {res['max_seq']:04d}")
            print(f"   本次生成: {new_code} = {res['prefix']} + {seq:04d}  "
                  f"(流水号 {res['max_seq']}+1)")
            print(f"   组合: 前缀{res['c']}{res['e']}{res['g']} + 流水号{seq:04d}")
            print("   正式 SQL 未写入；回填权威总表并重新同步后才正式生效。")
        print(f"[结果文件] {result_path}")
        con.close()
        return

    print("[错误] 未指定受支持的内部操作；必须通过 workflow.py 调用。")
    sys.exit(2)


if __name__ == '__main__':
    main()
