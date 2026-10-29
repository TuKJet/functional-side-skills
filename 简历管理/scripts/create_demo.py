"""Create an isolated, explicitly synthetic training pool. Never overwrite data."""
import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'skills/manage-hr-resumes/scripts'))
from hr_resume.service import Service


def create(root):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('演示目录必须为空；绝不向正式简历池添加演示资料')
    service = Service(root, '演示数据生成器')
    service.execute('init')
    jobs = []
    for department, title, criteria in [
        ('产品研发部', '机械设计工程师', [('机械设计项目经验', 60), ('SolidWorks 能力', 40)]),
        ('制造工程部', '工艺工程师', [('生产工艺改进经验', 70), ('质量问题分析经验', 30)]),
        ('职能管理部', '招聘专员', [('招聘执行经验', 60), ('Excel 台账维护能力', 40)]),
    ]:
        jobs.append(service.execute('jd-put', dict(department=department, title=title, month='2026-10', headcount=2,
            description='仅供培训的合成岗位，不代表实际招聘需求。' + '；'.join(c[0] for c in criteria),
            criteria=[dict(id=f'req-{i+1}', label=label, weight=weight, required=i == 0) for i, (label, weight) in enumerate(criteria)])))
    # Artifacts are retained inside the demo root, not business source fixtures.
    source = root / 'training-source'
    source.mkdir()
    for index, (job, evidence, status) in enumerate([
        (jobs[0], ['负责电机机械设计项目', '熟练使用 SolidWorks'], '二面'),
        (jobs[0], ['参与机械设计项目', '能够使用 SolidWorks 完成建模'], '待复核'),
        (jobs[1], ['负责生产工艺改进', '参与质量问题分析'], '初面'),
        (jobs[1], ['参与生产工艺改进', '独立完成质量问题分析'], '待筛选'),
        (jobs[2], ['执行招聘筛选和面试安排', '使用 Excel 维护招聘台账'], '待决定'),
        (jobs[2], ['协助招聘执行', '使用 Excel 维护进度'], '待筛选'),
    ]):
        path = source / f'演示候选人{index+1}.txt'
        path.write_text('合成培训资料，不是真实个人信息。\n' + '\n'.join(evidence), encoding='utf-8')
        candidate = service.execute('candidate-add', dict(name=f'示例候选人 {index+1:02}', source='培训演示', resume_path=str(path)))
        app = service.execute('application-add', dict(candidate_id=candidate['id'], job_id=job['id'], owner='演示·行政', notes='仅供体验流程'))
        if status != '待筛选':
            queue = next(q for q in service.execute('review-queue')['items'] if q['application_id'] == app['id'])
            service.execute('review-submit', dict(application_id=app['id'], application_version=app['version'], job_version=job['version'],
                resume_sha256=queue['resume_sha256'], text_sha256=queue['text_sha256'], reviewer='合成演示规则（不是实际 Agent 结论）',
                criteria=[dict(criterion_id=f'req-{i+1}', rating=3 if index % 2 else 4, evidence=quote, reason='培训用固定示例，正式审核由 Agent 逐项分析') for i, quote in enumerate(evidence)],
                strengths=['演示：有相关岗位经历描述'], risks=['演示：项目职责仍需核实'], questions=['请说明项目中独立完成的工作']))
            app = next(a for a in service.execute('snapshot')['applications'] if a['id'] == app['id'])
        service.execute('application-update', dict(id=app['id'], expected_version=app['version'], status=status, next_action_date='2026-10-30'))
        if status in ('二面', '待决定'):
            service.execute('interview-add', dict(application_id=app['id'], round=2, evaluator='演示·业务主管',
                summary='合成二面记录：能够说明项目过程，建议继续核实职责。', recommendation='待人工讨论', interview_date='2026-10-29', attachment_path=str(path)))
    return service.execute('ledger-export', dict(path=str(root / 'ledgers/演示招聘总台账.xlsx')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='独立空目录，禁止使用正式简历池')
    args = parser.parse_args()
    print(create(args.root))
