# -*- coding: utf-8 -*-
"""将物料清单 Excel 第一个工作表同步到 SQLite。

总表由人工维护，列顺序/表头文字可能变动，因此本脚本：
  1) 用「表头文字」而非固定列号定位各字段（容忍列重排）；
  2) 只读取第一个工作表；第二页及后续工作表不扫描、不导入、不参与流水号计算；
  3) 同步后做自检：表头结构、行数、编码/类别格式、抽样回查、重复/空值，
     任一结构性错误即打印映射明细并以退出码 1 失败，交由 agent 手动对齐。

仅允许由 workflow.py 在用户明确授权、完成备份后调用。
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from matlib import iter_sheet_rows, sheet_names, norm_code, connect, DEFAULT_DB
from workflow_guard import require_workflow_entry

# 表头文字 -> DB 字段（按文字定位；col_a 固定取最左列，因表头常为空或重复）
FIELD_BY_HEADER = {
    '大类': 'col_b', '大类编码': 'col_c', '一类': 'col_d',
    '一级子类编码': 'col_e', '二级': 'col_f', '二级子类编码': 'col_g',
    '上级分类-用友加': 'col_h', '旧物料编号': 'col_i', '新物料编码': 'col_j',
    '采购部门': 'col_k', '产品名称': 'name', '产品规格': 'spec',
    '单位': 'unit', '用途': 'usage',
}
# 缺了这些列，编码生成/检索将无法正常进行 -> 视为结构性错误
REQUIRED_FIELDS = ['col_c', 'col_e', 'col_g', 'col_j', 'name']
FIELD_LABEL = {
    'col_a': '引用(A)', 'col_b': '大类', 'col_c': '大类编码', 'col_d': '一类',
    'col_e': '一级子类编码', 'col_f': '二级', 'col_g': '二级子类编码',
    'col_h': '上级分类-用友加', 'col_i': '旧物料编号', 'col_j': '新物料编码',
    'col_k': '采购部门', 'name': '产品名称', 'spec': '产品规格',
    'unit': '单位', 'usage': '用途',
}
EXPECTED_COLS = ['id', 'excel_row', 'col_a', 'col_b', 'col_c', 'col_d', 'col_e',
                 'col_f', 'col_g', 'col_h', 'col_i', 'col_j', 'col_k', 'name',
                 'spec', 'unit', 'usage', 'retired']


def map_headers(header):
    """返回 (field->列号, 缺失的必需字段)。col_a 固定=最左列(index 0)。"""
    f2i = {'col_a': 0}
    for idx, h in enumerate(header):
        h = (h or '').strip()
        if h in FIELD_BY_HEADER:
            f2i.setdefault(FIELD_BY_HEADER[h], idx)  # 取首个匹配，容忍重复表头
    missing = [f for f in REQUIRED_FIELDS if f not in f2i]
    return f2i, missing


def _cell(row, f2i, field):
    idx = f2i.get(field)
    if idx is None or idx >= len(row):
        return ''
    v = row[idx]
    return v.strip() if v else ''


def iter_records(rows, f2i, stats, retired=False):
    """流式解析工作表数据行；仅有新物料编码 J 的行进入正式 SQL 镜像。"""
    for rix, row in enumerate(rows, start=2):
        j = _cell(row, f2i, 'col_j')
        if not j:
            name = _cell(row, f2i, 'name')
            if name:
                # 用户规则：只有 A-K 全空才是“本次待申请”。
                # A-K 已有分类/部门但 J 缺失的行是数据异常，单独报告。
                a_to_k_fields = (
                    'col_a', 'col_b', 'col_c', 'col_d', 'col_e', 'col_f',
                    'col_g', 'col_h', 'col_i', 'col_j', 'col_k')
                if any(_cell(row, f2i, field) for field in a_to_k_fields):
                    stats['classified_named_no_code'] += 1
                    if len(stats['classified_no_code_sample']) < 20:
                        stats['classified_no_code_sample'].append((rix, name))
                else:
                    stats['pending_named_no_code'] += 1
            continue
        record = dict(
            excel_row=rix,
            a=_cell(row, f2i, 'col_a'), b=_cell(row, f2i, 'col_b'),
            c=norm_code(_cell(row, f2i, 'col_c')), d=_cell(row, f2i, 'col_d'),
            e=norm_code(_cell(row, f2i, 'col_e')), f=_cell(row, f2i, 'col_f'),
            gg=norm_code(_cell(row, f2i, 'col_g')), h=_cell(row, f2i, 'col_h'),
            i=_cell(row, f2i, 'col_i'), j=j, k=_cell(row, f2i, 'col_k'),
            name=_cell(row, f2i, 'name'), spec=_cell(row, f2i, 'spec'),
            unit=_cell(row, f2i, 'unit'),
            usage=(_cell(row, f2i, 'usage') if not retired else ''),
            retired=1 if retired else 0)
        stats['count'] += 1
        if len(stats['sample']) < 20:
            stats['sample'].append(record)
        yield record


def verify_sync(con, main_sheet, main_hdr, m_f2i, m_missing, main_stats):
    """同步后自检。返回 (errors, warns, info)。errors 非空即需人工对齐。"""
    errors, warns, info = [], [], []

    # 1) SQL 表结构
    cols = [r[1] for r in con.execute("PRAGMA table_info(materials)")]
    miss_cols = [c for c in EXPECTED_COLS if c not in cols]
    if miss_cols:
        errors.append(f'SQL 表 materials 缺少列: {miss_cols}')

    # 2) 总表头结构 + 映射（只使用流式读取的首行，不重复加载整页）
    for f in m_missing:
        errors.append(f'主表[{main_sheet}] 缺少必需列: {FIELD_LABEL[f]}')
    info.append(('主表', main_sheet, m_f2i, main_hdr))

    # 3) 行数一致性
    db_main = con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    if db_main != main_stats['count']:
        errors.append(f'第一工作表行数不一致: SQL={db_main} 解析={main_stats["count"]}')
    db_ret = con.execute("SELECT COUNT(*) FROM materials WHERE retired!=0").fetchone()[0]
    if db_ret:
        errors.append(f'SQL 中出现 {db_ret} 条非第一工作表记录')

    # 4) 编码/类别格式约束（仅在相关列存在时检查，避免结构性缺失时噪声爆量）
    if 'col_j' not in m_missing:
        bad_j = con.execute(
            "SELECT col_j FROM materials WHERE length(col_j)!=10 "
            "OR col_j GLOB '*[^0-9]*'").fetchall()
        if bad_j:
            errors.append(f'存在 {len(bad_j)} 条「新物料编码」非10位数字: '
                          f'{[r[0] for r in bad_j[:5]]}')
    if not ({'col_c', 'col_e', 'col_g'} & set(m_missing)):
        bad_ceg = con.execute(
            "SELECT col_j,col_c,col_e,col_g FROM materials WHERE "
            "col_c GLOB '*[^0-9]*' OR length(col_c)!=2 OR "
            "col_e GLOB '*[^0-9]*' OR length(col_e)!=2 OR "
            "col_g GLOB '*[^0-9]*' OR length(col_g)!=2").fetchall()
        if bad_ceg:
            errors.append(f'存在 {len(bad_ceg)} 条「大类/一级/二级子类编码」非2位数字: '
                          f'{[(r[0], r[1], r[2], r[3]) for r in bad_ceg[:5]]}')

    # 5) 用例抽样：取前若干有效行，回查 SQL 比对关键字段（防写入错位）
    for r in main_stats['sample']:
        row = con.execute(
            "SELECT col_c,col_e,col_g,name,spec,unit FROM materials WHERE col_j=?",
            (r['j'],)).fetchone()
        if not row:
            warns.append(f'抽样未命中 SQL: {r["j"]}')
            continue
        if (row[0], row[1], row[2], row[3]) != (r['c'], r['e'], r['gg'], r['name']):
            errors.append(f'抽样字段不一致 {r["j"]}: '
                          f'SQL={row} 解析={(r["c"], r["e"], r["gg"], r["name"])}')

    # 6) 数据质量（非致命，提示用）
    empty_name = con.execute(
        "SELECT COUNT(*) FROM materials WHERE name=''").fetchone()[0]
    if empty_name:
        warns.append(f'有 {empty_name} 条「产品名称」为空（源表可能本就缺，非格式错误）')
    dups = con.execute(
        "SELECT col_j,COUNT(*) c FROM materials GROUP BY col_j HAVING c>1").fetchall()
    if dups:
        warns.append(f'源表存在 {len(dups)} 个重复编码(多为历史数据, 非脚本产生): '
                     f'{[r[0] for r in dups[:5]]}')
    if main_stats['classified_named_no_code']:
        samples = [f'第{row_no}行 {name}'
                   for row_no, name in main_stats['classified_no_code_sample']]
        warns.append(
            f'有 {main_stats["classified_named_no_code"]} 条记录 A-K 已有内容但 J 编码为空，'
            f'不当作本次待申请，也不导入 SQL: {samples}')

    return errors, warns, info


def print_report(errors, warns, info):
    print('\n==== 同步验证报告 ====')
    for tag, sheet, f2i, hdr in info:
        print(f'[{tag}] {sheet}')
        for field in ['col_a', 'col_b', 'col_c', 'col_d', 'col_e', 'col_f',
                      'col_g', 'col_h', 'col_i', 'col_j', 'col_k', 'name',
                      'spec', 'unit', 'usage']:
            idx = f2i.get(field)
            if idx is None or idx >= len(hdr):
                print(f'    {FIELD_LABEL[field]:<14} -> [缺失]')
            else:
                print(f'    {FIELD_LABEL[field]:<14} -> 第{idx}列 {hdr[idx]!r}')
    print('----')
    if warns:
        print('[警告] 非致命，供参考:')
        for w in warns:
            print('   -', w)
    if errors:
        print('[错误] 需人工对齐:')
        for e in errors:
            print('   -', e)
        print('结论: 验证未通过 —— 请 agent 手动检查总表格式，'
              '按需修改 sync_db.py 的 FIELD_BY_HEADER / map_headers 映射后重试。')
    else:
        print('[通过] 表头结构 / 行数 / 编码与类别格式 均与总表一致。')
    print('======================')


def main():
    require_workflow_entry()
    ap = argparse.ArgumentParser()
    ap.add_argument('--excel', required=True)
    ap.add_argument('--db', default=DEFAULT_DB)
    args = ap.parse_args()

    if not os.path.exists(args.excel):
        print(f"[错误] Excel 不存在: {args.excel}")
        sys.exit(1)

    names = sheet_names(args.excel)
    if not names:
        print("[错误] Excel 不包含工作表")
        sys.exit(1)
    # 用户规则：只读取工作簿顺序中的第一个工作表。
    # sheet_names 只解析 workbook.xml 以获取首页名称，不读取后续工作表的单元格数据。
    main_sheet = names[0]

    main_rows = iter(iter_sheet_rows(args.excel, main_sheet))
    try:
        main_hdr = next(main_rows)
    except StopIteration:
        print(f"[错误] 主表为空: {main_sheet}")
        sys.exit(1)
    m_f2i, m_missing = map_headers(main_hdr)

    main_stats = {
        'count': 0,
        'pending_named_no_code': 0,
        'classified_named_no_code': 0,
        'classified_no_code_sample': [],
        'sample': [],
    }

    con = connect(args.db)
    con.commit()
    # 差异报告（与现有库对比，以 Excel 为准）
    old = {r[0]: r[1:] for r in con.execute(
        "SELECT col_j, col_h, name, spec, retired FROM materials")}
    insert_sql = """INSERT INTO materials(
        excel_row,col_a,col_b,col_c,col_d,col_e,col_f,col_g,col_h,col_i,col_j,
        col_k,name,spec,unit,usage,retired)
        VALUES(:excel_row,:a,:b,:c,:d,:e,:f,:gg,:h,:i,:j,:k,
               :name,:spec,:unit,:usage,:retired)"""
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM materials")
        con.executemany(
            insert_sql, iter_records(main_rows, m_f2i, main_stats, retired=False))

        new_map = {r[0]: tuple(r[1:]) for r in con.execute(
            "SELECT col_j, col_h, name, spec, retired FROM materials")}
        added = [j for j in new_map if j not in old]
        removed = [j for j in old if j not in new_map]
        changed = [j for j in new_map if j in old and tuple(old[j]) != new_map[j]]

        errors, warns, info = verify_sync(
            con, main_sheet, main_hdr, m_f2i, m_missing, main_stats)
        print_report(errors, warns, info)
        if errors:
            con.rollback()
            print("[已回滚] 同步验证失败，原数据库保持不变。")
            con.close()
            sys.exit(1)

        con.execute("INSERT OR REPLACE INTO meta VALUES('source_excel',?)", (args.excel,))
        con.execute("INSERT OR REPLACE INTO meta VALUES('synced_at',?)",
                    (datetime.datetime.now().isoformat(timespec='seconds'),))
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise

    print(f"[完成] 流式同步 {os.path.basename(args.excel)} -> {args.db}")
    print(f"  第一工作表({main_sheet}): {main_stats['count']} 条已有编码物料")
    print(f"  本次待申请: {main_stats['pending_named_no_code']} 条（名称非空、A-K 全空）")
    print(f"  异常缺码: {main_stats['classified_named_no_code']} 条（A-K 已有内容但 J 为空）")
    print("  后续工作表: 未读取、未导入、未参与流水号计算")
    if old:
        print(f"  差异: 新增 {len(added)} / 移除 {len(removed)} / 变更 {len(changed)}")
        for tag, lst in (('新增', added), ('移除', removed), ('变更', changed)):
            for j in lst[:10]:
                print(f"    [{tag}] {j}")
            if len(lst) > 10:
                print(f"    [{tag}] ... 共 {len(lst)} 条")
    con.close()


if __name__ == '__main__':
    main()
