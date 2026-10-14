"""Product lifecycle, governance, history and bounded exports."""
import csv
import io
import json
import os
import sqlite3
import time
from flask import Blueprint,Response,current_app,g,request
from pydantic import ValidationError
from api_v1 import ok,error
from schemas.qualification import ReasonRequest
from services.access_policy import actor_identity
from services.aoi_stream_service import StreamError
from services.admission import CapacityError
from tasks.qualification_store import QualificationError
from tasks.review_store import ReviewStore
from config import APP_VERSION
from services.pagination import pagination

def page(default=100,maximum=1000):
    return pagination(request.args,default,maximum)

def product_id_valid(product_id):
    if not isinstance(product_id,str) or not 1<=len(product_id.strip())<=200:
        raise ValueError('product_id 必须是 1..200 字符')
    return product_id.strip()

def create_operations_blueprint(store,streams,coordinator,qualifications,folders,training):
    api=Blueprint('operations',__name__,url_prefix='/api/v1')
    reviews=ReviewStore(qualifications,store)
    api.review_store=reviews

    @api.errorhandler(ValueError)
    @api.errorhandler(ValidationError)
    def invalid(exc):
        if isinstance(exc,QualificationError): return error(exc.code,str(exc),exc.status)
        if isinstance(exc,ValidationError): return error('INVALID_ARGUMENT',exc.errors(include_url=False)[0]['msg'],400)
        return error('INVALID_ARGUMENT',str(exc),400)

    @api.errorhandler(StreamError)
    def stream_failure(exc): return error(exc.code,str(exc),exc.status)

    @api.get('/live')
    def live(): return ok({'alive':True})

    @api.get('/ready/<stage>')
    def stage_ready(stage):
        states={'stage1':coordinator.stage1.health_check(),'stage2':'ok' if len(coordinator.prediction.available_channels())==8 else 'model_missing',
                'stage3':coordinator.stage3.health_check()}
        if stage not in states: return error('INVALID_ARGUMENT','stage 必须是 stage1、stage2 或 stage3',400)
        status=states[stage]
        unavailable=current_app.extensions['draining'].is_set() or current_app.extensions['capacity'].snapshot()['status']=='critical'
        return ok(dict(stage=stage,status=status,ready=status=='ok' and not unavailable),200 if status=='ok' and not unavailable else 503)

    @api.get('/system/info')
    def system_info():
        databases={}
        for name,path in {'tasks':store.index.path,'aoi':streams.journal.path,'qualifications':qualifications.path,'sources':folders.registry.path}.items():
            with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as db:
                version=db.execute('PRAGMA user_version').fetchone()[0]
            databases[name]=dict(status='ok',schema_version=version,bytes=path.stat().st_size)
        return ok(dict(version=APP_VERSION,api_version='v1',environment=current_app.config['APP_ENV'],
            started_at=current_app.extensions['started_at'],uptime_seconds=round(time.monotonic()-current_app.extensions['started_monotonic'],1),
            databases=databases,queues=coordinator.executor.stats(),training_queue=training.capacity(),aoi=streams.health(),
            disk=current_app.extensions['capacity'].snapshot(),draining=current_app.extensions['draining'].is_set(),
            snapshots={'failed_writes':store.snapshot_errors,'audit_mirror_failed_writes':store.audit_mirror_errors},
            stages={'stage1':coordinator.stage1.health_check(),'stage2':coordinator.prediction.available_channels(),'stage3':coordinator.stage3.health_check()}))

    def history_filters():
        filters={key:request.args[key] for key in ('product_id','task_id','stage','status','channel','source_id','from_time','to_time') if key in request.args}
        if getattr(g,'liante_device_id',None): filters['owner_device_id']=g.liante_device_id
        if 'channel' in filters and not 0<=int(filters['channel'])<=7: raise ValueError('channel 必须是 0..7')
        return filters

    @api.get('/tasks')
    def task_history():
        limit,offset=page();items,total=store.index.list_tasks(history_filters(),limit,offset)
        return ok(dict(items=items,total=total,limit=limit,offset=offset,order='newest_first'))

    @api.post('/tasks/<task_id>/retry')
    def retry_task(task_id):
        previous=store.get(task_id)
        if previous is None: return error('TASK_NOT_FOUND','任务不存在',404)
        if previous['stage']!='stage2' or previous['status'] not in {'FAILED','CANCELLED','TIMEOUT'}:
            return error('TASK_NOT_RETRYABLE','仅失败、取消或超时的 Stage2 任务可以重试',409)
        payload=request.get_json(silent=True) or {}
        if not isinstance(payload,dict) or set(payload)-{'request_id'}: raise ValueError('retry 只接受 request_id；新输入使用 folder 接口')
        if not previous.get('input_files'): return error('TASK_NOT_RETRYABLE','历史任务缺少可重试输入记录，请从来源重新提交',409)
        try: reservation=coordinator.executor.reserve('stage2')
        except CapacityError:
            response,status=error('QUEUE_FULL','队列已满，请稍后重试',429);response.headers['Retry-After']='2';return response,status
        new=None
        try:
            if previous.get('source_id'):
                folders.source(previous['source_id'],getattr(g,'liante_device_id',None))
            files=previous['input_files']
            coordinator.prediction.batch_work(files)
            new=store.create('stage2',previous['product_id'],payload.get('request_id'),
                retry_of=task_id,source_id=previous.get('source_id'),source_revision=previous.get('source_revision'),
                owner_device_id=previous.get('owner_device_id'),input_directory=previous.get('input_directory'))
            coordinator.queue_stage2(new['task_id'],files,reservation,continue_on_error=previous.get('continue_on_error',True))
            store.safe_log(new['task_id'],'INFO','retry','原输入快照重试',retry_of=task_id)
            return ok(dict(task_id=new['task_id'],retry_of=task_id,status='QUEUED'),202)
        except Exception:
            if new: store.update(new['task_id'],status='FAILED',error_code='QUEUE_SUBMISSION_FAILED',error_message='重试提交失败')
            raise
        finally: reservation.release()

    @api.post('/aoi/streams/<task_id>/products')
    def open_product(task_id):
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or set(payload)-{'product_id','required_views','request_id'}: raise ValueError('产品参数无效')
        return ok(streams.open_product(task_id,product_id_valid(payload.get('product_id')),payload.get('required_views')),201)

    @api.get('/aoi/streams/<task_id>/products')
    def stream_products(task_id):
        if not streams.journal.stream(task_id): return error('STREAM_NOT_FOUND','流不存在',404)
        limit,offset=page();items,total=streams.journal.products(stream_id=task_id,limit=limit,offset=offset)
        return ok(dict(items=items,total=total,limit=limit,offset=offset))

    @api.get('/aoi/streams/<task_id>/products/<product_id>')
    def stream_product(task_id,product_id):
        value=streams.journal.product(task_id,product_id)
        return ok(value) if value else error('PRODUCT_NOT_FOUND','产品不存在',404)

    @api.post('/aoi/streams/<task_id>/products/<product_id>/close')
    def close_product(task_id,product_id): return ok(streams.close_product(task_id,product_id))

    @api.get('/qualifications')
    def qualification_history():
        limit,offset=page();items,total=qualifications.history(request.args.get('product_id'),getattr(g,'liante_device_id',None),limit,offset)
        return ok(dict(items=items,total=total,limit=limit,offset=offset))

    @api.get('/products/<product_id>/qualifications')
    def product_qualifications(product_id):
        limit,offset=page();items,total=qualifications.history(product_id,getattr(g,'liante_device_id',None),limit,offset)
        return ok(dict(items=items,total=total,limit=limit,offset=offset))

    @api.post('/qualifications/<qualification_id>/revoke')
    def revoke(qualification_id):
        reason=ReasonRequest.model_validate(request.get_json(silent=True))
        return ok(qualifications.revoke(qualification_id,actor_identity(),reason.reason))

    @api.post('/qualifications/<qualification_id>/replace')
    def replace(qualification_id):
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict): raise ValueError('替换参数必须是对象')
        reason=ReasonRequest.model_validate({'reason':payload.get('reason'),'request_id':payload.get('request_id')})
        return ok(qualifications.create({k:v for k,v in payload.items() if k!='reason'},actor_identity(),
                                       replace_id=qualification_id,replacement_reason=reason.reason),201)

    @api.get('/qualifications/<qualification_id>/history')
    def evidence_history(qualification_id):
        if not qualifications.get(qualification_id): return error('QUALIFICATION_NOT_FOUND','证据不存在',404)
        limit,offset=page();items,total=qualifications.events(qualification_id,limit,offset)
        return ok(dict(items=items,total=total,limit=limit,offset=offset))

    @api.post('/reviews')
    def create_review(): return ok(reviews.create(request.get_json(silent=True),actor_identity()),201)

    @api.get('/reviews')
    def review_list():
        limit,offset=page();items,total=reviews.list(request.args.get('product_id'),limit,offset)
        return ok(dict(items=items,total=total,limit=limit,offset=offset))

    @api.get('/reviews/<review_id>')
    def review_get(review_id):
        value=reviews.get(review_id)
        return ok(value) if value else error('REVIEW_NOT_FOUND','复核记录不存在',404)

    def trace(product_id,limit,offset):
        owner=getattr(g,'liante_device_id',None)
        filters={'product_id':product_id}
        if owner: filters['owner_device_id']=owner
        tasks,total=store.index.list_tasks(filters,limit,offset)
        products,product_total=streams.journal.products(product_id=product_id,limit=limit,offset=offset,owner=owner)
        evidence,evidence_total=qualifications.history(product_id,owner,limit,offset)
        decisions,review_total=reviews.list(product_id,limit,offset) if not owner else ([],0)
        if owner and not (total or product_total or evidence_total): raise QualificationError('设备无权访问该产品','SOURCE_FORBIDDEN',403)
        # No prediction arrays or arbitrary full result objects in the trace response.
        evidence=[{k:v for k,v in row.items() if k in {'qualification_id','product_id','aoi_task_id','stage2_task_id','status',
            'created_at','updated_at','expires_at','recipe_id','recipe_version','recorded_by','reviewer','superseded_by','reason'}} for row in evidence]
        stage2=[];file_budget=100;preview_budget=100
        for row in tasks:
            if row['stage']!='stage2': continue
            task=store.get(row['task_id'])
            files=(task.get('result') or {}).get('files',[])
            summaries=[]
            for f in files[:file_budget]:
                preview=(f.get('predictions') or [])[:min(3,preview_budget)]
                preview_budget-=len(preview);file_budget-=1
                summaries.append({**{k:f.get(k) for k in ('file_index','channel','status','prediction_count','model_version','recipe_version','predictions_endpoint')},'preview':preview})
            stage2.append(dict(task_id=row['task_id'],status=row['status'],files=summaries,files_truncated=len(summaries)<len(files)))
        return dict(product_id=product_id,tasks=dict(items=tasks,total=total),stage2_results=stage2,aoi_products=dict(items=products,total=product_total),
            aoi_tasks=[{k:v for k,v in task.items() if k not in {'result','input_files'}} for task in
                       (store.get(sid) for sid in dict.fromkeys(p['stream_id'] for p in products)) if task],
            qualifications=dict(items=evidence,total=evidence_total),reviews=dict(items=decisions,total=review_total),limit=limit,offset=offset)

    @api.get('/products/<product_id>/trace')
    def product_trace(product_id):
        limit,offset=page(50,200);return ok(trace(product_id_valid(product_id),limit,offset))

    def csv_response(rows,name):
        text=io.StringIO(newline='');fields=list(dict.fromkeys(k for row in rows for k in row))
        writer=csv.DictWriter(text,fieldnames=fields);writer.writeheader()
        for row in rows:
            # CSV spreadsheet formula injection protection; numeric values stay numeric.
            writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v)
                             for k,v in row.items()})
        response=Response('\ufeff'+text.getvalue(),mimetype='text/csv')
        response.headers['Content-Disposition']=f'attachment; filename="{name}.csv"';return response

    @api.get('/exports/tasks.csv')
    def export_tasks():
        limit,offset=page(100,1000);items,total=store.index.list_tasks(history_filters(),limit,offset)
        rows=[{k:v for k,v in row.items() if not isinstance(v,(dict,list))} for row in items]
        response=csv_response(rows,'tasks');response.headers['X-Total-Count']=str(total);return response

    @api.get('/exports/tasks/<task_id>/predictions.csv')
    def export_predictions(task_id):
        task=store.get(task_id)
        if not task or task['stage']!='stage2': return error('TASK_NOT_FOUND','Stage2 任务不存在',404)
        limit,offset=page(1000,1000);file_index=int(request.args.get('file_index',1));channel=int(request.args.get('channel',0))
        if file_index<1 or not 0<=channel<=7:raise ValueError('文件序号或通道无效')
        items,total=store.index.predictions(task_id,file_index,channel,limit,offset)
        response=csv_response(items,'predictions');response.headers['X-Total-Count']=str(total);return response

    @api.get('/exports/qualifications/<qualification_id>.json')
    def export_qualification(qualification_id):
        value=qualifications.get(qualification_id)
        return ok(value) if value else error('QUALIFICATION_NOT_FOUND','证据不存在',404)

    @api.get('/exports/products/<product_id>/trace.json')
    def export_trace(product_id):
        limit,offset=page(100,200);return ok(trace(product_id_valid(product_id),limit,offset))

    return api
