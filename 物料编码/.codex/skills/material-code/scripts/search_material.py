# -*- coding: utf-8 -*-
"""在 SQLite 物料库中检索固定 Top5 在用物料候选。

用法:
仅允许由 workflow.py 调用。

评分 = 名称 + 规格 + 类别路径 + 用途，按 H(六位类别) 去重保证多样性。
核心逻辑见 matlib.search_top。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from matlib import connect, DEFAULT_DB, search_top
from workflow_guard import require_workflow_entry


def main():
    require_workflow_entry()
    ap = argparse.ArgumentParser()
    ap.add_argument('query')
    ap.add_argument('--db', default=DEFAULT_DB)
    args = ap.parse_args()

    con = connect(args.db)
    if not con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]:
        print("[错误] 物料库为空，请先运行 sync_db.py 同步 Excel。")
        sys.exit(1)
    picked = search_top(con, args.query)
    con.close()

    if not picked:
        print(f"[无结果] 库内未找到与「{args.query}」相关的物料，请换关键词或确认类别。")
        return

    print(f"查询「{args.query}」Top {len(picked)} 候选（供选择参考类别）:")
    print('-' * 100)
    for n, (s, r) in enumerate(picked, 1):
        # r: id,excel_row,a,b,c,d,e,f,g,h,i,j,k,name,spec,unit,usage,retired
        (rid, xrow, a, b, c, d, e, f, g, h, i, j, k,
         name, spec, unit, usage, retired) = r
        print(f"[{n}] id={rid} 编码={j}  分类: {b}({c}) > {d}({e}) > {f}({g})  H={h}")
        print(f"    名称: {name or '-'} | 规格: {spec or '-'} | 单位: {unit or '-'} | 采购: {k or '-'} | 相似度: {s:.3f}")
    print('-' * 100)
    print("候选仅供分类参考；新编码必须回到申请 job，由用户明确选择后通过 workflow.py commit 生成。")


if __name__ == '__main__':
    main()
