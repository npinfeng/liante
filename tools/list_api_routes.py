"""Inventory actual Flask registrations (including static and blueprint routes)."""
from __future__ import annotations
import argparse
import inspect
import json
import os
import re
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.access_policy import roles_for

def canonical(path): return re.sub(r'<(?:[^:<>]+:)?([^<>]+)>',r'{\1}',path)

def collect(app):
    root=Path(__file__).resolve().parents[1];rows=[]
    for rule in app.url_map.iter_rules():
        function=app.view_functions[rule.endpoint]
        source=inspect.getsourcefile(function) or 'Flask static'
        try: line=inspect.getsourcelines(function)[1]
        except (OSError,TypeError): line=0
        try: source=str(Path(source).relative_to(root))
        except ValueError: source='flask/app.py' if rule.endpoint=='static' else Path(source).name
        for method in sorted(rule.methods-{'HEAD','OPTIONS'}):
            rows.append(dict(method=method,path=canonical(rule.rule),endpoint=rule.endpoint,
                blueprint=rule.endpoint.split('.')[0] if '.' in rule.endpoint else '',
                auth=roles_for(method,rule.rule),source=source,line=line,
                operation_id=rule.endpoint.replace('.','_')+'_'+method.lower()))
    return sorted(rows,key=lambda r:(r['path'],r['method']))

def isolated_inventory():
    # No working database, queue recovery, model artifact writes or network listener.
    with tempfile.TemporaryDirectory(prefix='liante-routes-') as temporary:
        for key,value in {'LIANTE_FACTORY_ONLY':'1','APP_ENV':'development','OUTPUT_DIR':temporary+'/output',
            'LOG_DIR':temporary+'/logs','DATA_DIR':temporary+'/data','STAGE2_INPUT_DIRS':temporary+'/input',
            'LIANTE_ADMIN_TOKEN':'','LIANTE_OPERATOR_TOKEN':'','LIANTE_DEVICE_TOKEN':'','LIANTE_DEVICE_TOKENS':'{}'}.items():
            os.environ[key]=value
        from backend_server import create_app
        app=create_app(model_dir=temporary+'/models',data_dir=temporary+'/train')
        try: return collect(app)
        finally:
            app.extensions['inference'].executor.shutdown(wait=True)
            app.extensions['task_manager'].shutdown(wait=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',action='store_true');parser.add_argument('--markdown',action='store_true')
    args=parser.parse_args();rows=isolated_inventory()
    if args.json: print(json.dumps({'count':len(rows),'routes':rows},ensure_ascii=False,indent=2))
    elif args.markdown:
        print('|Method|Path|Endpoint|Blueprint|Auth|Source|\n|---|---|---|---|---|---|')
        for row in rows: print('|'+ '|'.join(str(row[k]) if k!='auth' else ', '.join(row[k]) for k in ('method','path','endpoint','blueprint','auth','source'))+'|')
        print(f'\nTotal: {len(rows)}')
    else:
        for row in rows: print(f"{row['method']:5} {row['path']:80} {row['endpoint']} {','.join(row['auth'])} {row['source']}:{row['line']}")
        print(f'Total: {len(rows)}')
if __name__=='__main__': main()
