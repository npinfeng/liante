"""Software acceptance without training, weights or synthetic model adapters."""
import hashlib
import io
import json
import socket
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
from threading import Event
import pytest
from PIL import Image
import backend_server
import api_v1
from deploy.backup_restore import backup,restore
from schemas.qualification import QualificationRequest,Measurement
from services.aoi_stream_service import AOIStreamManager,Frame,StreamError
from services.inference_service import ModelAdapterService,InferenceError
from services.capacity import CapacityMonitor,DiskCapacityError
from tasks.inference_store import InferenceTaskStore
from tasks.state_machine import validate_transition,TERMINAL
from tools.check_task_consistency import check

@pytest.fixture
def system(tmp_path,monkeypatch):
    import config
    for key,name in [('OUTPUT_DIR','output'),('LOG_DIR','logs'),('DATA_DIR','data')]:
        monkeypatch.setattr(backend_server,key,tmp_path/name)
        if hasattr(api_v1,key):monkeypatch.setattr(api_v1,key,tmp_path/name)
    incoming=tmp_path/'input';incoming.mkdir()
    monkeypatch.setattr(backend_server,'STAGE2_INPUT_DIRS',str(incoming))
    monkeypatch.setattr(backend_server,'STAGE2_ALLOWED_DIRS',str(incoming))
    monkeypatch.setattr(backend_server,'STAGE2_FILE_STABLE_SECONDS',0)
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('DISK_WARNING_FREE_BYTES','0');monkeypatch.setenv('DISK_CRITICAL_FREE_BYTES','0')
    secrets={name:uuid.uuid4().hex for name in ['admin','operator','CAM-A','CAM-B','MES-A','MES-B']}
    monkeypatch.setattr(backend_server,'ADMIN_TOKEN',secrets['admin'])
    monkeypatch.setattr(backend_server,'OPERATOR_TOKEN',secrets['operator'])
    monkeypatch.setattr(backend_server,'DEVICE_TOKEN','')
    devices={k:v for k,v in secrets.items() if k not in {'admin','operator'}}
    monkeypatch.setattr(backend_server,'DEVICE_TOKENS',devices);monkeypatch.setattr(api_v1,'DEVICE_TOKENS',devices)
    app=backend_server.create_app(model_dir=tmp_path/'models',data_dir=tmp_path/'train')
    def auth(name='admin'):
        return {'Authorization':'Bearer '+secrets[name],'X-Liante-Role':name if name in {'admin','operator'} else 'device',
                **({} if name in {'admin','operator'} else {'X-Liante-Device-ID':name})}
    yield app.test_client(),app,auth,tmp_path
    streams=app.extensions['aoi_streams']
    for stream in list(streams._streams):streams.stop(stream)
    deadline=time.monotonic()+5
    while streams.health()['active_streams'] and time.monotonic()<deadline:time.sleep(.01)
    app.extensions['inference'].executor.shutdown(wait=True);app.extensions['task_manager'].shutdown(wait=True)

def png(color='white'):
    buffer=io.BytesIO();Image.new('RGB',(2,2),color).save(buffer,format='PNG');return buffer.getvalue()

def wait(predicate):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.02)
    raise AssertionError('software worker did not settle')

def stream(client,auth,**extra):
    response=client.post('/api/v1/aoi/streams',headers=auth('CAM-A'),json={'source_id':'CAM-A','product_id':'P1',**extra})
    assert response.status_code==201,response.json
    return response.json['data']['task_id']

def push(client,auth,sid,sequence,product='P1',view='TOP',data=None):
    return client.post(f'/api/v1/aoi/streams/{sid}/frames',headers=auth('CAM-A'),
        data={'sequence':str(sequence),'product_id':product,'view_type':view,'image':(io.BytesIO(data or png()),'frame.png')},
        content_type='multipart/form-data')

@pytest.mark.parametrize('path',['/api/v1/live','/api/v1/health','/health'])
def test_public_liveness(system,path):
    client,app,auth,root=system
    assert client.get(path).status_code==200

@pytest.mark.parametrize('stage',['stage1','stage3'])
def test_missing_model_readiness_is_truthful(system,stage):
    client,app,auth,root=system
    response=client.get('/api/v1/ready/'+stage)
    assert response.status_code==503 and response.json['data']['status']=='not_configured'
    assert client.get('/api/v1/ready').status_code==503

@pytest.mark.parametrize('role',['operator','MES-A','CAM-A'])
def test_admin_only_routes(system,role):
    client,app,auth,root=system
    assert client.get('/api/v1/system/info',headers=auth(role)).status_code==403
    assert client.post('/api/v1/admin/drain',headers=auth(role)).status_code==403
    assert client.post('/automl/train',headers=auth(role),json={}).status_code==403

def test_system_info_redacts_secrets_and_paths(system):
    client,app,auth,root=system
    response=client.get('/api/v1/system/info',headers=auth())
    assert response.status_code==200
    assert str(root) not in response.get_data(as_text=True)
    assert {'databases','queues','disk','stages','uptime_seconds'}<=response.json['data'].keys()

def test_common_idempotency_conflict_and_identity(system):
    client,app,auth,root=system
    body={'stage':'stage2','product_id':'P','request_id':'R1'}
    first=client.post('/api/v1/tasks',headers=auth(),json=body)
    same=client.post('/api/v1/tasks',headers=auth(),json=body)
    assert first.status_code==201 and same.status_code==200
    assert first.json['data']['task_id']==same.json['data']['task_id'] and same.json['data']['replayed']
    changed=client.post('/api/v1/tasks',headers=auth(),json={**body,'product_id':'OTHER'})
    assert changed.status_code==409 and changed.json['code']=='REQUEST_REPLAY_CONFLICT'
    another=client.post('/api/v1/tasks',headers=auth('operator'),json=body)
    assert another.status_code==201 and another.json['data']['task_id']!=first.json['data']['task_id']

def test_idempotency_survives_receipt_response_gap(system,monkeypatch):
    client,app,auth,root=system
    receipts=app.extensions['idempotency'];original=receipts.save
    def fail_response(*args,**kwargs):
        if kwargs.get('db') is None:raise sqlite3.OperationalError('injected response receipt failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(receipts,'save',fail_response)
    body={'stage':'stage2','product_id':'P','request_id':'ACK-GAP'}
    first=client.post('/api/v1/tasks',headers=auth(),json=body)
    repeated=client.post('/api/v1/tasks',headers=auth(),json=body)
    assert first.json['data']['task_id']==repeated.json['data']['task_id']
    assert app.extensions['inference_store'].index.list_tasks()[1]==1

@pytest.mark.parametrize('target',['COMPLETED','FAILED','CANCELLED','TIMEOUT','REVIEW_REQUIRED'])
def test_terminal_cas_rejects_late_callbacks(tmp_path,target):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');task=store.create('stage2','STATE')
    store.update(task['task_id'],status='RUNNING')
    initial=store.update(task['task_id'],status=target,result={'unit_state':'recorded'})
    assert store.update(task['task_id'],status='FAILED',result={'unit_state':'late'})==initial

@pytest.mark.parametrize('a,b',[('COMPLETED','RUNNING'),('PENDING','COMPLETED'),('CANCEL_REQUESTED','COMPLETED'),('FAILED','QUEUED')])
def test_invalid_state_edges(a,b):
    with pytest.raises(ValueError):validate_transition(a,b)

def test_terminal_transition_race(tmp_path):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');task=store.create('stage2','RACE')
    store.update(task['task_id'],status='RUNNING')
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda status:store.update(task['task_id'],status=status),['FAILED','CANCELLED','TIMEOUT','COMPLETED']))
    assert len({value['status'] for value in results})==1

def test_snapshot_failure_preserves_sqlite_and_safe_repair(tmp_path,monkeypatch):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs')
    import tasks.inference_store as module
    real=module.os.replace
    monkeypatch.setattr(module.os,'replace',lambda *args:(_ for _ in ()).throw(OSError('injected disk full')))
    task=store.create('stage2','ACK')
    assert store.get(task['task_id']) and store.snapshot_errors
    assert check(tmp_path/'output')['status']=='WARNING'
    monkeypatch.setattr(module.os,'replace',real)
    assert check(tmp_path/'output',repair_snapshots=True)['status']=='OK'

def test_database_failure_has_no_ack(system,monkeypatch):
    client,app,auth,root=system
    def broken(*args):raise sqlite3.OperationalError('injected database unavailable')
    monkeypatch.setattr(app.extensions['inference_store'].index,'write_task',broken)
    response=client.post('/api/v1/tasks',headers=auth(),json={'stage':'stage2','product_id':'FAIL'})
    assert response.status_code==500 and response.json['code']=='INTERNAL_ERROR'

def test_task_lookup_never_reads_external_json(tmp_path):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs')
    assert store.get('../outside') is None and store.get(str(tmp_path/'outside')) is None

def test_aoi_multiple_products_close_views_and_absent_model(system):
    client,app,auth,root=system;sid=stream(client,auth,view_types=['TOP','SIDE'],required_views=['TOP','SIDE'])
    assert push(client,auth,sid,1).status_code==202
    assert push(client,auth,sid,2,view='SIDE').status_code==202
    closed=client.post(f'/api/v1/aoi/streams/{sid}/products/P1/close',headers=auth('CAM-A'))
    assert closed.status_code==200 and not closed.json['data']['missing_views']
    assert push(client,auth,sid,3,product='P2').status_code==202
    incomplete=client.post(f'/api/v1/aoi/streams/{sid}/products/P2/close',headers=auth('CAM-A'))
    assert incomplete.json['data']['status']=='INCOMPLETE' and incomplete.json['data']['missing_views']==['SIDE']
    assert client.get(f'/api/v1/aoi/streams/{sid}',headers=auth('CAM-A')).json['data']['stream']['accepting']
    wait(lambda:app.extensions['aoi_streams'].journal.get(sid,2)['status']=='FAILED')
    value=app.extensions['aoi_streams'].journal.get(sid,1)
    assert value['result']['error_code']=='MODEL_NOT_CONFIGURED' and 'output' not in value['result']
    assert app.extensions['aoi_streams'].journal.product(sid,'P1')['status']=='FAILED'

@pytest.mark.parametrize('change',['image','product','view'])
def test_aoi_frame_replay_conflict(system,change):
    client,app,auth,root=system;sid=stream(client,auth,view_types=['TOP','SIDE'],required_views=['TOP'])
    assert push(client,auth,sid,1).status_code==202
    assert push(client,auth,sid,1).json['data']['replayed']
    different=push(client,auth,sid,1,data=png('black') if change=='image' else None,
                   product='P2' if change=='product' else 'P1',view='SIDE' if change=='view' else 'TOP')
    assert different.status_code==409 and different.json['code']=='REQUEST_REPLAY_CONFLICT'
    assert app.extensions['aoi_streams'].journal.records(sid)[1]==1

def test_replay_after_product_and_stream_close(system):
    client,app,auth,root=system;sid=stream(client,auth,view_types=['TOP'],required_views=['TOP'])
    assert push(client,auth,sid,1).status_code==202
    client.post(f'/api/v1/aoi/streams/{sid}/products/P1/close',headers=auth('CAM-A'))
    wait(lambda:app.extensions['aoi_streams'].journal.get(sid,1)['status']=='FAILED')
    client.post(f'/api/v1/aoi/streams/{sid}/close',headers=auth('CAM-A'))
    assert push(client,auth,sid,1).json['data']['replayed']

@pytest.mark.parametrize('suffix',['','/frames','/products','/products/P1','/close','/products/P1/close'])
def test_aoi_device_idor(system,suffix):
    client,app,auth,root=system;sid=stream(client,auth)
    response=client.post(f'/api/v1/aoi/streams/{sid}{suffix}',headers=auth('CAM-B')) if suffix.endswith('/close') else client.get(f'/api/v1/aoi/streams/{sid}{suffix}',headers=auth('CAM-B'))
    assert response.status_code==403

@pytest.mark.parametrize('parent',['present','missing','failed'])
def test_aoi_ack_recovery_without_models(tmp_path,parent):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');sid=uuid.uuid4().hex
    if parent!='missing':
        task=store.create('stage1','P',task_id=sid)
        store.update(sid,status='RUNNING',result={'stream_id':sid,'source_id':'CAM','accepting':False})
        if parent=='failed':store.update(sid,status='FAILED',error_code='FRAME_STORAGE_FAILED')
    from services.frame_journal import FrameJournal
    journal=FrameJournal(tmp_path/'data')
    journal.register_stream(sid,dict(product_id='P',source_id='CAM',view_types=['V'],required_views=['V']))
    frame=Frame(1,'P',png(),'image/png','.png',hashlib.sha256(png()).hexdigest(),'V')
    journal.insert(sid,frame);journal.mark_running(sid,1);journal.close_product(sid,'P');journal.close_stream(sid)
    restored=InferenceTaskStore(tmp_path/'output',tmp_path/'logs')
    manager=AOIStreamManager(restored,ModelAdapterService('stage1','',''),tmp_path/'data')
    wait(lambda:journal.get(sid,1)['status']=='FAILED')
    wait(lambda:not manager._streams)
    assert journal.product(sid,'P')['status']=='FAILED' and restored.get(sid)

def test_aoi_journal_failure_rejects_unacknowledged_frame(system,monkeypatch):
    client,app,auth,root=system;sid=stream(client,auth)
    monkeypatch.setattr(app.extensions['aoi_streams'].journal,'insert',lambda *args:(_ for _ in ()).throw(sqlite3.OperationalError('injected disk full')))
    response=push(client,auth,sid,1,view='UNSPECIFIED')
    assert response.status_code==503 and response.json['code']=='FRAME_STORAGE_FAILED'
    assert app.extensions['aoi_streams'].journal.records(sid)[1]==0

def test_capacity_warning_and_critical(system,monkeypatch):
    client,app,auth,root=system;monitor=app.extensions['capacity']
    from collections import namedtuple
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr('services.capacity.shutil.disk_usage',lambda path:usage(10000,8500,1500))
    monitor.warning_ratio=.2;monitor.critical_ratio=.1;monitor.warning_bytes=monitor.critical_bytes=0
    assert monitor.snapshot()['status']=='warning'
    assert client.post('/api/v1/tasks',headers=auth(),json={'stage':'stage2','product_id':'WARN'}).status_code==201
    monkeypatch.setattr('services.capacity.shutil.disk_usage',lambda path:usage(10000,9500,500))
    rejected=client.post('/api/v1/tasks',headers=auth(),json={'stage':'stage2','product_id':'CRITICAL'})
    assert rejected.status_code==507 and rejected.json['code']=='DISK_CAPACITY_LOW' and rejected.headers['Retry-After']
    assert client.get('/api/v1/live').status_code==200

def test_drain_keeps_queries_and_product_close(system):
    client,app,auth,root=system;sid=stream(client,auth)
    assert client.post('/api/v1/admin/drain',headers=auth()).status_code==200
    response=client.post('/api/v1/tasks',headers=auth(),json={'stage':'stage2','product_id':'NEW'})
    assert response.status_code==503 and response.json['code']=='SERVICE_DRAINING'
    assert client.get('/api/v1/tasks',headers=auth()).status_code==200
    assert client.post(f'/api/v1/aoi/streams/{sid}/close',headers=auth('CAM-A')).status_code==202 or sid not in app.extensions['aoi_streams']._streams

def test_review_without_model_records_null_grade_and_authenticated_actor(system):
    client,app,auth,root=system
    body={'product_id':'P','qualification_id':None,'stage3_task_id':None,'decision':'APPROVE','comment':'待接入模型的人工记录','request_id':'REVIEW-1'}
    response=client.post('/api/v1/reviews',headers=auth('operator'),json=body)
    assert response.status_code==201 and response.json['data']['final_grade'] is None and response.json['data']['suggested_grade'] is None
    assert response.json['data']['reviewer']=='operator'
    assert client.post('/api/v1/reviews',headers=auth('operator'),json=body).json['data']['replayed']
    assert client.post('/api/v1/reviews',headers=auth('operator'),json={**body,'request_id':'BAD','final_grade':'A'}).status_code==409

@pytest.mark.parametrize('payload',[{'name':'power','unit':'dBm','value':float('nan')},
    {'name':'voltage','unit':'V','value':10,'upper':5},{'name':'v','unit':'','value':1}])
def test_measurement_validation(payload):
    with pytest.raises(ValueError):Measurement.model_validate(payload)

def seed_manual_lifecycle(app,product='P',owner='MES-A',age=0):
    # This is an isolated lifecycle row, never a fabricated AOI/Stage3 inference output.
    qid=uuid.uuid4().hex;stamp=(datetime.now(timezone.utc)+timedelta(hours=age)).isoformat()
    path=app.extensions['qualifications'].path
    with sqlite3.connect(path) as db:
        db.execute('''INSERT INTO qualifications(qualification_id,product_id,aoi_task_id,stage2_task_id,evidence_json,created_at,
            status,updated_at,expires_at,owner_device_id) VALUES (?,?,?,?,?,?,'ACTIVE',?,?,?)''',
            (qid,product,uuid.uuid4().hex,uuid.uuid4().hex,json.dumps({'product_id':product}),stamp,stamp,
             (datetime.now(timezone.utc)+timedelta(hours=age+1)).isoformat(),owner))
    return qid

def test_qualification_expiry_revoke_and_history(system):
    client,app,auth,root=system
    old=seed_manual_lifecycle(app,age=-3);active=seed_manual_lifecycle(app)
    assert client.get('/api/v1/qualifications/'+old,headers=auth()).json['data']['status']=='EXPIRED'
    rejected=client.post('/api/v1/inference/grading',headers=auth(),json={'product_id':'P','qualification_id':old})
    assert rejected.status_code==409 and rejected.json['code']=='QUALIFICATION_EXPIRED'
    revoke=client.post('/api/v1/qualifications/'+active+'/revoke',headers=auth('operator'),json={'reason':'人工测量需重新复核'})
    assert revoke.json['data']['status']=='REVOKED'
    assert client.post('/api/v1/inference/grading',headers=auth(),json={'product_id':'P','qualification_id':active}).json['code']=='QUALIFICATION_REVOKED'
    history=client.get('/api/v1/qualifications/'+active+'/history',headers=auth()).json['data']
    assert history['items'][0]['actor']=='operator' and history['items'][0]['operation']=='revoke'

@pytest.mark.parametrize('suffix',['','/history'])
def test_qualification_device_idor(system,suffix):
    client,app,auth,root=system;qid=seed_manual_lifecycle(app)
    assert client.get('/api/v1/qualifications/'+qid+suffix,headers=auth('MES-B')).status_code==403
    assert client.get('/api/v1/exports/qualifications/'+qid+'.json',headers=auth('MES-B')).status_code==403

def test_qualification_rejects_client_qualified_shortcut(system):
    client,app,auth,root=system
    assert client.post('/api/v1/qualifications',headers=auth(),json={'product_id':'P','qualified':True}).status_code==422
    assert client.post('/api/v1/inference/grading',headers=auth(),json={'product_id':'P','qualified':True}).status_code==400

def test_history_trace_exports_are_paged_and_owned(system):
    client,app,auth,root=system;store=app.extensions['inference_store']
    for index in range(5):store.create('stage2','P',owner_device_id='MES-A',source_id='SOURCE-A')
    store.create('stage2','FOREIGN',owner_device_id='MES-B')
    response=client.get('/api/v1/tasks?product_id=P&limit=2&offset=2',headers=auth('MES-A')).json['data']
    assert response['total']==5 and len(response['items'])==2 and all('result' not in row for row in response['items'])
    trace=client.get('/api/v1/products/P/trace?limit=2',headers=auth('MES-A')).json['data']
    assert trace['tasks']['total']==5 and len(trace['tasks']['items'])==2
    assert client.get('/api/v1/products/P/trace',headers=auth('MES-B')).status_code==403
    assert client.get('/api/v1/exports/tasks.csv?limit=1001',headers=auth()).status_code==400
    assert client.get('/api/v1/exports/tasks.csv?limit=2',headers=auth('MES-A')).headers['X-Total-Count']=='5'

def test_stage2_retry_keeps_input_and_creates_new_task(system):
    client,app,auth,root=system;store=app.extensions['inference_store'];path=root/'input/channel_0.csv'
    path.write_text('a,b,c,d\n1,2,3,4\n',encoding='utf-8')
    old=store.create('stage2','P',owner_device_id='MES-A');store.update(old['task_id'],status='RUNNING',input_files=[{'file_path':str(path),'channels':[0]}])
    store.update(old['task_id'],status='FAILED',error_code='MODEL_NOT_CONFIGURED')
    response=client.post('/api/v1/tasks/'+old['task_id']+'/retry',headers=auth('MES-A'),json={'request_id':'RETRY-1'})
    assert response.status_code==202
    new=response.json['data']['task_id'];assert new!=old['task_id']
    wait(lambda:store.get(new)['status']=='FAILED')
    assert store.get(old['task_id'])['status']=='FAILED' and store.get(new)['retry_of']==old['task_id']
    assert client.post('/api/v1/tasks/'+old['task_id']+'/retry',headers=auth('MES-B'),json={}).status_code==403
    assert client.post('/api/v1/tasks/'+old['task_id']+'/retry',headers=auth('MES-A'),json={'request_id':'RETRY-1'}).json['data']['task_id']==new

def test_mes_sources_no_model_logs_and_ownership(system):
    client,app,auth,root=system;path=root/'input/channel_0.csv';path.write_text('a,b,c,d\n1,2,3,4\n',encoding='utf-8')
    base='/api/v1/inference/ea-bias';body={'source_id':'LINE-A','directory':str(path.parent),'request_id':'SOURCE-1'}
    assert client.post(base+'/sources',headers=auth('MES-A'),json=body).status_code==201
    assert client.post(base+'/sources',headers=auth('MES-A'),json=body).json['data']['replayed']
    assert client.post(base+'/scan',headers=auth('MES-B'),json={'source_id':'LINE-A'}).status_code==403
    scanned=client.post(base+'/scan',headers=auth('MES-A'),json={'source_id':'LINE-A'})
    assert scanned.status_code==200 and scanned.json['data']['missing_model_files']==1
    logs=client.get(base+'/sources/LINE-A/logs',headers=auth('MES-A')).json['data']['items']
    assert logs and all('Authorization' not in str(row) for row in logs)

def test_backup_restore_integrity_and_running_target_guard(tmp_path):
    source=tmp_path/'source';store=InferenceTaskStore(source/'output',source/'logs')
    task=store.create('stage2','BACKUP');store.update(task['task_id'],status='RUNNING');store.update(task['task_id'],status='FAILED')
    archive=tmp_path/'backup.zip';manifest=backup(source,archive,port=65530)
    target=tmp_path/'restore';restore(archive,target,port=65530)
    with sqlite3.connect(target/'output/tasks/index.sqlite3') as db:
        assert db.execute('SELECT status FROM tasks').fetchone()[0]=='FAILED'
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1)
    try:
        with pytest.raises(RuntimeError):restore(archive,target,port=server.getsockname()[1],replace_existing=True)
    finally:server.close()
    assert all('model' not in path for path in manifest['files'])

def test_restore_rejects_corrupt_manifest(tmp_path):
    import zipfile
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as saved:saved.writestr('manifest.json',json.dumps({'format_version':999}))
    with pytest.raises(ValueError):restore(archive,tmp_path/'target',port=65530)

def test_qualification_replace_atomic_lifecycle_and_audit(system,monkeypatch):
    _,app,_,_=system;store=app.extensions['qualifications'];old=seed_manual_lifecycle(app)
    # Unit-test the transactional lifecycle using metadata only, never model outputs.
    evidence={'product_id':'P','aoi_task_id':uuid.uuid4().hex,'stage2_task_id':uuid.uuid4().hex,'notes':'人工记录版本迁移单元测试'}
    expiry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    original=store.event
    def failed_event(*args,**kwargs):raise sqlite3.OperationalError('injected audit write failure')
    monkeypatch.setattr(store,'event',failed_event)
    with pytest.raises(sqlite3.OperationalError):store._commit_evidence(evidence,expiry,'operator',old,'测量记录更正')
    assert store.get(old)['status']=='ACTIVE' and store.history('P')[1]==1
    monkeypatch.setattr(store,'event',original)
    new=store._commit_evidence(evidence,expiry,'operator',old,'测量记录更正')
    assert store.get(old)['status']=='SUPERSEDED' and store.get(old)['superseded_by']==new['qualification_id']
    assert store.events(old,10,0)[0][0]['operation']=='replace'

def test_aoi_backpressure_and_duplicate_before_capacity(system,monkeypatch):
    client,app,auth,_=system;streams=app.extensions['aoi_streams'];streams.queue_capacity=1
    entered,release=Event(),Event()
    def delayed_failure(payload):
        entered.set();release.wait(5);raise InferenceError('MODEL_NOT_CONFIGURED','测试故障注入：模型不可用',503)
    monkeypatch.setattr(streams.adapter,'predict',delayed_failure)
    sid=stream(client,auth,view_types=['TOP'],required_views=['TOP'])
    try:
        assert push(client,auth,sid,1).status_code==202 and entered.wait(5)
        assert push(client,auth,sid,2,product='P2').status_code==202
        blocked=push(client,auth,sid,3,product='P3')
        assert blocked.status_code==429 and blocked.headers['Retry-After']
        assert push(client,auth,sid,2,product='P2').json['data']['replayed']
        assert streams.journal.records(sid)[1]==2
    finally:release.set()

def test_restart_interrupted_tasks_use_sqlite_without_snapshot(tmp_path):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');task=store.create('stage2','RESTART')
    store.update(task['task_id'],status='QUEUED');(store.output_dir/(task['task_id']+'.json')).unlink()
    restored=InferenceTaskStore(tmp_path/'output',tmp_path/'logs')
    assert restored.get(task['task_id'])['status']=='FAILED' and restored.get(task['task_id'])['error_code']=='SERVICE_RESTARTED'

def test_jsonl_failure_keeps_authoritative_event(tmp_path,monkeypatch):
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');task=store.create('stage2','LOG')
    original=Path.open
    def fail_log(path,*args,**kwargs):
        if path.suffix=='.jsonl':raise OSError('injected log disk full')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail_log)
    store.log(task['task_id'],'WARNING','unit_audit','业务事件已写入 SQLite')
    assert store.logs(task['task_id'])[1]==2 and store.audit_mirror_errors==1

def test_training_state_persists_without_training_a_model(tmp_path):
    from tasks.task_manager import TrainingTaskManager
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');manager=TrainingTaskManager(store=store)
    def software_failure(update):
        update(status='preparing',progress=5,message='纯软件任务故障注入')
        raise RuntimeError('no training performed')
    try:
        task_id=manager.submit(0,1,software_failure)
        wait(lambda:store.get(task_id)['status']=='FAILED')
        assert manager.get(task_id)['status']=='failed' and store.get(task_id)['stage']=='automl'
    finally:manager.shutdown(wait=True)

@pytest.mark.parametrize('value',['production-debug','production-no-auth'])
def test_production_fails_closed(tmp_path,monkeypatch,value):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setattr(backend_server,'DEBUG',value=='production-debug')
    monkeypatch.setattr(backend_server,'ADMIN_TOKEN','')
    with pytest.raises(ValueError):backend_server.create_app(model_dir=tmp_path/'models')


def test_review_receipt_survives_response_capture_crash(system):
    client,app,auth,_=system
    payload={'product_id':'RECEIPT','decision':'APPROVE','comment':'幂等事务恢复检查','request_id':'DOMAIN-RECOVERY'}
    first=client.post('/api/v1/reviews',headers=auth('operator'),json=payload)
    assert first.status_code==201
    with app.extensions['inference_store'].index.connect() as db:
        db.execute("DELETE FROM request_receipts WHERE request_id=?",('DOMAIN-RECOVERY',))
    second=client.post('/api/v1/reviews',headers=auth('operator'),json=payload)
    assert second.status_code==200 and second.json['data']['replayed']
    assert second.json['data']['review_id']==first.json['data']['review_id']
    assert app.extensions['reviews'].list('RECEIPT')[1]==1
    changed=client.post('/api/v1/reviews',headers=auth('operator'),json={**payload,'comment':'不同的内容'})
    assert changed.status_code==409 and changed.json['code']=='REQUEST_REPLAY_CONFLICT'


def test_expiry_with_east_asia_offset(system):
    _,app,_,_=system;store=app.extensions['qualifications'];qid=seed_manual_lifecycle(app)
    expiry=(datetime.now(timezone(timedelta(hours=8)))-timedelta(minutes=1)).isoformat()
    with store.connect() as db:db.execute('UPDATE qualifications SET expires_at=? WHERE qualification_id=?',(expiry,qid))
    assert store.get(qid)['status']=='EXPIRED'


def test_trace_owned_product_page_past_end(system):
    client,app,auth,_=system
    app.extensions['inference_store'].create('stage2','PAGED',owner_device_id='MES-A')
    response=client.get('/api/v1/products/PAGED/trace?offset=100',headers=auth('MES-A'))
    assert response.status_code==200 and response.json['data']['tasks']=={'items':[],'total':1}


def test_training_reservation_released_when_task_write_fails(tmp_path,monkeypatch):
    from tasks.task_manager import TrainingTaskManager
    store=InferenceTaskStore(tmp_path/'output',tmp_path/'logs');manager=TrainingTaskManager(max_waiting_jobs=0,store=store)
    def fail(*args,**kwargs):raise sqlite3.OperationalError('injected task write failure')
    monkeypatch.setattr(store,'create',fail)
    try:
        with pytest.raises(sqlite3.OperationalError):manager.submit(0,1,lambda _:None)
        assert manager.capacity()['outstanding']==0
    finally:manager.shutdown(wait=True)


def test_restore_rolls_back_all_installed_files(tmp_path,monkeypatch):
    import deploy.backup_restore as module
    source=tmp_path/'source';target=tmp_path/'target'
    store=InferenceTaskStore(source/'output',source/'logs');store.create('stage2','SOURCE')
    with sqlite3.connect(source/'output/qualifications.sqlite3') as db:db.execute('CREATE TABLE unit_history(id INTEGER)')
    archive=tmp_path/'backup.zip';backup(source,archive,port=65530)
    old=InferenceTaskStore(target/'output',target/'logs');old.create('stage2','OLD')
    before=module.digest(target/'output/tasks/index.sqlite3');original=module.os.replace;count=0
    def fail_second(src,dst):
        nonlocal count
        count+=1
        if count==2:raise OSError('injected install failure')
        return original(src,dst)
    monkeypatch.setattr(module.os,'replace',fail_second)
    with pytest.raises(OSError):restore(archive,target,port=65530,replace_existing=True)
    assert module.digest(target/'output/tasks/index.sqlite3')==before
    assert not (target/'output/qualifications.sqlite3').exists()


def test_log_page_aliases_and_bounds(system):
    client,app,auth,_=system;store=app.extensions['inference_store'];task=store.create('stage2','PAGE')
    for n in range(3):store.log(task['task_id'],'INFO','unit',str(n))
    path='/api/v1/tasks/'+task['task_id']+'/logs'
    response=client.get(path+'?page=2&page_size=2',headers=auth())
    assert response.status_code==200 and response.json['data']['offset']==2 and len(response.json['data']['items'])==2
    assert client.get(path+'?page=0',headers=auth()).status_code==400
    assert client.get(path+'?page_size=1001',headers=auth()).status_code==400
    assert client.get(path+'?page=1&offset=1',headers=auth()).status_code==400


def test_windows_deploy_scripts_utf8_bom():
    root=Path(__file__).resolve().parents[1]
    for name in ['lifecycle.ps1','run_profile.ps1','register_service.ps1','start_c209.ps1']:
        assert (root/'deploy'/name).read_bytes().startswith(b'\xef\xbb\xbf')


def test_terminal_audit_rejects_late_completion(system):
    _,app,_,_=system;store=app.extensions['inference_store'];coordinator=app.extensions['inference']
    task=store.create('stage2','AUDIT-RACE')
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _:coordinator._commit_terminal(task['task_id'],'FAILED','ERROR','故障注入终态',error_code='UNIT_FAILURE'),range(2)))
    assert store.logs(task['task_id'])[1]==2
    coordinator._commit_terminal(task['task_id'],'COMPLETED','INFO','不应写入的迟到完成事件')
    assert store.get(task['task_id'])['status']=='FAILED' and store.logs(task['task_id'])[1]==2


def test_header_request_id_is_the_task_request_id(system):
    client,app,auth,_=system;headers={**auth(),'X-Request-ID':'HEADER-ONLY'}
    first=client.post('/api/v1/tasks',headers=headers,json={'stage':'stage2','product_id':'HEADER'})
    assert first.status_code==201 and first.json['data']['request_id']=='HEADER-ONLY'
    second=client.post('/api/v1/tasks',headers=headers,json={'stage':'stage2','product_id':'HEADER'})
    assert second.json['data']['replayed'] and second.json['data']['task_id']==first.json['data']['task_id']
    conflict=client.post('/api/v1/tasks',headers=headers,json={'stage':'stage2','product_id':'HEADER','request_id':'DIFFERENT'})
    assert conflict.status_code==400
