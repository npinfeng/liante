"""Read-only SQLite / JSON / event / result consistency audit.

Optional --repair-snapshots rebuilds only missing or stale JSON from authoritative
SQLite payloads. It never changes a task status, result, model or input file.
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

def check(output,logs=None,repair_snapshots=False):
    directory=Path(output)/'tasks';path=directory/'index.sqlite3';findings=[]
    if not path.is_file(): return {'status':'ERROR','findings':[{'code':'TASK_DATABASE_MISSING'}]}
    with sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': findings.append({'severity':'ERROR','code':'DATABASE_INTEGRITY'})
        ids=set()
        for task_id,status,payload in db.execute('SELECT task_id,status,task_json FROM tasks'):
            ids.add(task_id)
            if not payload:
                findings.append(dict(severity='ERROR',code='TASK_PAYLOAD_MISSING',task_id=task_id));continue
            try: task=json.loads(payload)
            except ValueError:
                findings.append(dict(severity='ERROR',code='TASK_PAYLOAD_INVALID',task_id=task_id));continue
            if task.get('status')!=status: findings.append(dict(severity='ERROR',code='INDEX_STATUS_MISMATCH',task_id=task_id))
            snapshot=directory/f'{task_id}.json'
            try: same=json.loads(snapshot.read_text(encoding='utf-8'))==task
            except (OSError,ValueError): same=False
            if not same:
                if repair_snapshots:
                    temporary=snapshot.with_suffix('.tmp');temporary.write_text(json.dumps(task,ensure_ascii=False),encoding='utf-8');os.replace(temporary,snapshot)
                else: findings.append(dict(severity='WARNING',code='SNAPSHOT_MISSING_OR_STALE',task_id=task_id))
            if status=='COMPLETED' and task.get('result') is None:
                findings.append(dict(severity='ERROR',code='COMPLETED_RESULT_MISSING',task_id=task_id))
            if task.get('stage')=='stage2' and task.get('result'):
                for file in task['result'].get('files',[]):
                    if file.get('status')=='FAILED':continue
                    count=db.execute('SELECT COUNT(*) FROM prediction_rows WHERE task_id=? AND file_index=? AND channel=?',
                        (task_id,file.get('file_index',1),file.get('channel',0))).fetchone()[0]
                    if count!=file.get('prediction_count',count): findings.append(dict(severity='ERROR',code='PREDICTION_COUNT_MISMATCH',task_id=task_id))
        for code,table in (('ORPHAN_RESULT','prediction_rows'),('ORPHAN_EVENT','events')):
            for (task_id,) in db.execute(f'SELECT DISTINCT task_id FROM {table} WHERE task_id NOT IN (SELECT task_id FROM tasks)'):
                findings.append(dict(severity='ERROR',code=code,task_id=task_id))
        for snapshot in directory.glob('*.json'):
            if snapshot.stem not in ids:findings.append(dict(severity='WARNING',code='JSON_WITHOUT_DATABASE_TASK',task_id=snapshot.stem))
        if logs:
            for log in (Path(logs)/'tasks').glob('*.jsonl'):
                if log.stem not in ids:findings.append(dict(severity='WARNING',code='LOG_WITHOUT_DATABASE_TASK',task_id=log.stem))
    status='ERROR' if any(f.get('severity')=='ERROR' for f in findings) else 'WARNING' if findings else 'OK'
    return dict(status=status,task_count=len(ids),findings=findings,repaired_snapshots=bool(repair_snapshots))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=Path('output'))
    parser.add_argument('--logs',type=Path,default=Path('logs'));parser.add_argument('--repair-snapshots',action='store_true')
    args=parser.parse_args();result=check(args.output,args.logs,args.repair_snapshots)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 2 if result['status']=='ERROR' else 0
if __name__=='__main__':raise SystemExit(main())
