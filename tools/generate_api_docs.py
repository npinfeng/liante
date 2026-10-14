"""Generate API.md and OpenAPI from the actual Flask route inventory."""
from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.list_api_routes import isolated_inventory
from tools.api_catalog_legacy import ENDPOINTS

ROOT=Path(__file__).resolve().parents[1]
PAGED={'operations.task_history','operations.stream_products','operations.qualification_history',
    'operations.product_qualifications','operations.evidence_history','operations.review_list','operations.product_trace',
    'operations.export_tasks','operations.export_predictions','operations.export_trace',
    'api_v1.logs','api_v1.paged_predictions','api_v1.aoi_frame_records','api_v1.stage2_source_logs'}
DESCRIPTIONS={
 'operations.live':('系统','服务存活','仅检测 Web 进程存活；不依赖任何模型。'),
 'operations.stage_ready':('系统','阶段就绪','独立检查阶段模型、排空和临界磁盘状态；未就绪 503。'),
 'operations.system_info':('系统','运行信息','管理员读取版本、数据库版本、队列、磁盘和阶段状态；不返回秘密或绝对路径。'),
 'operations.task_history':('Task','任务历史','按产品、任务、阶段、状态、通道、来源与时间筛选；服务端分页。'),
 'operations.retry_task':('Task','重试 Stage2','失败、取消、超时的任务使用原输入快照创建新任务，保留 retry_of；文件变化会明确失败。'),
 'operations.open_product':('AOI','建立产品','在流内建立独立产品并确定 required_views，不能修改已接收产品的要求。'),
 'operations.stream_products':('AOI','流内产品','分页查询产品生命周期、帧状态统计和缺失视角。'),
 'operations.stream_product':('AOI','产品状态','查看流内单个产品，独立于流任务状态。'),
 'operations.close_product':('AOI','关闭产品','停止接收该产品的新帧，流继续接收其他产品；缺视角为 INCOMPLETE，缺模型帧为 FAILED。'),
 'operations.qualification_history':('Qualification','合格证据列表','分页读取版本化证据；设备只读取归属自己的记录。'),
 'operations.product_qualifications':('Qualification','产品证据历史','分页读取同一产品的 ACTIVE、SUPERSEDED、REVOKED、EXPIRED 证据。'),
 'operations.revoke':('Qualification','撤销证据','记录身份和原因；已撤销重放，已替换证据不能再次撤销。'),
 'operations.replace':('Qualification','替换证据','原 ACTIVE 证据与新证据同事务替换；新证据仍须通过全部校验。'),
 'operations.evidence_history':('Logs','证据审计','分页读取创建、过期、撤销、替换和复核事件。'),
 'operations.create_review':('Review','登记人工复核','保存认证身份、决定、说明和关联记录。真实分级结果缺失时等级必须为空，复核不生成模型结果。'),
 'operations.review_list':('Review','复核历史','按产品分页读取不可覆盖的复核决定。'),
 'operations.review_get':('Review','复核详情','读取单次复核记录。'),
 'operations.product_trace':('Product Trace','产品追溯','分页返回各阶段任务、流内产品、证据版本、复核关联与时间；不携带大型预测数组。'),
 'operations.export_tasks':('Export','任务 CSV','导出匹配筛选的当前页，最多 1000 条，防护电子表格公式注入。'),
 'operations.export_predictions':('Export','预测 CSV','按任务、文件和通道导出最多 1000 条真实已落盘预测行。'),
 'operations.export_qualification':('Export','证据 JSON','导出单个有界证据对象；设备归属检查与查询相同。'),
 'operations.export_trace':('Export','追溯 JSON','导出当前追溯分页，最多每类 200 条。'),
 'static':('Frontend','静态页面和资源','Flask path 转换器提供 index.html、operations.html、train.html 及本地脚本样式；计作一条实际注册路由。'),
}
OVERRIDES={
 'api_v1.create_task':'创建 SQLite 权威任务记录，不执行模型；外部预建任务不等于推理成功。',
 'api_v1.task':'读取 SQLite 任务；summary=1 隐藏 Stage2 的结果。',
 'api_v1.logs':'SQLite 结构化日志按事件升序分页；异常详情在非调试环境隐藏。',
 'api_v1.start_aoi_stream':'创建设备流，可持续接收多个产品；配置允许视角和默认必需视角。',
 'api_v1.push_aoi_frame':'先持久化帧和产品元数据再 ACK；相同序号、产品、校验和、视角重放，冲突 409。',
 'api_v1.create_qualification':'认证管理员或操作员登记有界人工测量；验证真实完成的 AOI 产品与 Stage2、产品、时间和配方。复核身份由服务器绑定。',
 'api_v1.grading':'使用服务端 ACTIVE 且当前有效的证据，入队与执行时复查。真实 Stage3 模型 NOT CONFIGURED 时返回 503，不产生等级。',
 'automl_task':'旧小写训练状态保持兼容，主存使用统一 SQLite 任务状态机。',
}

def error_codes():
    codes={'UNAUTHORIZED','FORBIDDEN','DEVICE_ID_REQUIRED','ORIGIN_DENIED','DISK_CAPACITY_LOW',
           'REQUEST_REPLAY_CONFLICT','REQUEST_HISTORY_FULL','SERVICE_DRAINING','SUBMISSION_BUSY',
           'QUALIFICATION_EXPIRED','QUALIFICATION_REVOKED','QUALIFICATION_SUPERSEDED','NOT_READY'}
    for directory in ('api','services','tasks'):
        paths=list((ROOT/directory).glob('*.py'))
        for path in paths+[ROOT/'api_v1.py',ROOT/'backend_server.py']:
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for call in ast.walk(tree):
                if not isinstance(call,ast.Call):continue
                name=call.func.id if isinstance(call.func,ast.Name) else ''
                index=1 if name=='QualificationError' else 0
                if name in {'error','api_error','StreamError','InferenceError','FolderInputError','QualificationError'} and len(call.args)>index:
                    value=call.args[index]
                    if isinstance(value,ast.Constant) and isinstance(value.value,str) and re.fullmatch('[A-Z][A-Z0-9_]+',value.value):codes.add(value.value)
    return sorted(codes)

def main():
    routes=isolated_inventory()
    from schemas.inference import BatchPredictRequest,FolderPredictRequest,Stage2SourceRequest
    from schemas.prediction import PredictRequest,FilePredictRequest
    from schemas.training import TrainRequest,TrainAllRequest
    from schemas.qualification import QualificationRequest,ReasonRequest,ReviewRequest
    components={}
    models={'api_v1.ea_bias':BatchPredictRequest,'api_v1.predict_stage2_folder':FolderPredictRequest,
        'api_v1.scan_stage2_folder':FolderPredictRequest,'api_v1.configure_stage2_source':Stage2SourceRequest,
        'api_v1.create_qualification':QualificationRequest,'operations.replace':QualificationRequest,
        'operations.revoke':ReasonRequest,'operations.create_review':ReviewRequest,
        'predict':PredictRequest,'predict_file':FilePredictRequest,'automl_train':TrainRequest,'automl_train_all':TrainAllRequest}
    for model in set(models.values()):
        schema=model.model_json_schema(ref_template='#/components/schemas/{model}')
        components.update(schema.pop('$defs',{}));components[model.__name__]=schema
    components['TaskCreate']={'type':'object','additionalProperties':False,'required':['stage','product_id'],
        'properties':{'stage':{'type':'string','enum':['stage1','stage2','stage3','automl']},'product_id':{'type':'string','minLength':1,'maxLength':200},'request_id':{'type':'string','minLength':1,'maxLength':200}}}
    components['StreamCreate']={'type':'object','required':['source_id','product_id'],'additionalProperties':False,'properties':{
        'source_id':{'type':'string','minLength':1,'maxLength':128},'product_id':{'type':'string','minLength':1,'maxLength':200},
        'request_id':{'type':'string','minLength':1,'maxLength':200},'view_types':{'type':'array','minItems':1,'maxItems':32,'uniqueItems':True,'items':{'type':'string','minLength':1,'maxLength':64}},
        'required_views':{'type':'array','minItems':1,'maxItems':32,'uniqueItems':True,'items':{'type':'string','minLength':1,'maxLength':64}}}}
    components['ProductCreate']={'type':'object','additionalProperties':False,'required':['product_id'],'properties':{
        'product_id':{'type':'string','minLength':1,'maxLength':200},'required_views':components['StreamCreate']['properties']['required_views'],
        'request_id':{'type':'string','minLength':1,'maxLength':200}}}
    components['RequestId']={'type':'object','additionalProperties':False,'properties':{'request_id':{'type':'string','minLength':1,'maxLength':200}}}
    components['GradingRequest']={'type':'object','additionalProperties':False,'required':['product_id','qualification_id'],'properties':{
        'product_id':{'type':'string','minLength':1,'maxLength':200},'qualification_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},'request_id':{'type':'string','minLength':1,'maxLength':200}}}
    components['Envelope']={'type':'object','required':['code','message','data'],'properties':{
        'code':{'oneOf':[{'type':'integer'},{'type':'string'}]},'message':{'type':'string'},'data':{},
        'request_id':{'type':'string'},'timestamp':{'type':'string','format':'date-time'},'replayed':{'type':'boolean'}}}
    from tools.api_response_schemas import install
    response_schemas=install(components)
    custom_body={'api_v1.create_task':'TaskCreate','api_v1.start_aoi_stream':'StreamCreate','operations.open_product':'ProductCreate',
        'api_v1.grading':'GradingRequest','operations.retry_task':'RequestId','operations.close_product':'RequestId',
        'api_v1.close_aoi_stream':'RequestId','api_v1.cancel_task':'RequestId','automl_cancel':'RequestId','begin_drain':'RequestId'}
    legacy={(row['method'],row['path']):row for row in ENDPOINTS}
    spec={'openapi':'3.1.0','info':{'title':'联特科技工业多模态检测 API','version':'2.0.0','description':f'实际 Flask 操作数 {len(routes)}，不含自动 HEAD/OPTIONS。Stage1/Stage3 模型 NOT CONFIGURED。'},
          'servers':[{'url':'http://localhost:8000','description':'按部署 profile 修改主机和端口'}],
          'paths':{},'components':{'securitySchemes':{'basicAuth':{'type':'http','scheme':'basic'},
              'bearerAuth':{'type':'http','scheme':'bearer'}},'schemas':components}}
    catalogs=[]
    for route in routes:
        endpoint=route['endpoint'];path=route['path'];old=legacy.get((route['method'],path),{})
        category,name,purpose=DESCRIPTIONS.get(endpoint,(old.get('category','System'),old.get('name',endpoint),old.get('purpose',endpoint)))
        purpose=OVERRIDES.get(endpoint,purpose)
        params=[]
        for key in re.findall(r'{([^}]+)}',path):
            schema={'type':'integer','minimum':1} if key=='sequence' else {'type':'string'}
            if key in {'task_id','qualification_id','review_id'}:schema.update(pattern='^[a-f0-9]{32}$')
            if key=='stage':schema['enum']=['stage1','stage2','stage3']
            if key=='product_id':schema.update(minLength=1,maxLength=200)
            params.append({'name':key,'in':'path','required':True,'schema':schema,'description':'资源标识；必须符合归属权限'})
        if endpoint in PAGED:
            maximum=200 if endpoint in {'operations.product_trace','operations.export_trace'} else 1000
            default_limit={'api_v1.logs':200,'operations.product_trace':50,'operations.export_predictions':1000}.get(endpoint,100)
            params.extend([{'name':'limit','in':'query','schema':{'type':'integer','minimum':1,'maximum':maximum,'default':default_limit},'description':f'每页 1..{maximum}'},
                           {'name':'offset','in':'query','schema':{'type':'integer','minimum':0,'maximum':10000000,'default':0},'description':'从 0 开始的偏移'}])
            if endpoint in PAGED:
                params.extend([{'name':'page','in':'query','schema':{'type':'integer','minimum':1},'description':'页码从 1 开始；与 limit/offset 互斥'},
                    {'name':'page_size','in':'query','schema':{'type':'integer','minimum':1,'maximum':maximum},'description':'分页条数，与 page 配合'}])
        queries=[]
        if endpoint in {'operations.task_history','operations.export_tasks'}:queries=['product_id','task_id','stage','status','channel','source_id','from_time','to_time']
        if endpoint in {'operations.qualification_history','operations.review_list'}:queries=['product_id']
        if endpoint in {'api_v1.paged_predictions','operations.export_predictions'}:queries=['file_index','channel']
        if endpoint=='api_v1.logs':queries=['stage']
        if endpoint=='api_v1.task':queries=['summary']
        for key in queries:
            schema={'type':'integer','minimum':0,'maximum':7} if key=='channel' else {'type':'integer','minimum':1} if key=='file_index' else {'type':'string','maxLength':200}
            if key in {'from_time','to_time'}:schema['format']='date-time'
            params.append({'name':key,'in':'query','schema':schema,'description':'可选筛选；时间使用带时区 ISO 8601'})
        if route['auth']!=['public']:
            params.extend([{'name':'X-Liante-Role','in':'header','schema':{'type':'string','enum':['admin','operator','device']},'description':'Bearer 身份角色，默认 device；Basic 用户名为角色'},
                           {'name':'X-Liante-Device-ID','in':'header','schema':{'type':'string','maxLength':128},'description':'设备调用必须提供独立已配置设备 ID'}])
        if route['method']=='POST': params.append({'name':'X-Request-ID','in':'header','schema':{'type':'string','minLength':1,'maxLength':200},'description':'幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等'})
        schema_name=models[endpoint].__name__ if endpoint in models else custom_body.get(endpoint)
        body={'$ref':'#/components/schemas/'+schema_name} if schema_name else None
        if endpoint=='operations.replace':
            body={'allOf':[{'$ref':'#/components/schemas/'+schema_name}], 'description':'与 QualificationRequest 相同并增加必填 reason；见 ReplaceQualification'}
            replacement=json.loads(json.dumps(components['QualificationRequest']));replacement['properties']['reason']={'type':'string','minLength':1,'maxLength':2000};replacement['required'].append('reason')
            components['ReplaceQualification']=replacement;body={'$ref':'#/components/schemas/ReplaceQualification'}
        media='application/json'
        if endpoint=='api_v1.push_aoi_frame':
            media='multipart/form-data';body={'type':'object','required':['image'],'properties':{'image':{'type':'string','format':'binary'},
                'sequence':{'type':'integer','minimum':1},'frame_seq':{'type':'integer','minimum':1},'product_id':{'type':'string','minLength':1,'maxLength':200},'view_type':{'type':'string','maxLength':64}},
                'anyOf':[{'required':['sequence']},{'required':['frame_seq']}], 'description':'序号从 1 连续递增，sequence 与 frame_seq 二选一；多视角必须传 view_type；图片默认上限 2 MiB'}
        if endpoint=='api_v1.aoi':
            body={'type':'object','required':['product_id'],'properties':{'product_id':{'type':'string','maxLength':200},'image_path':{'type':'string','maxLength':1000},'request_id':{'type':'string','maxLength':200}},'description':'旧单帧入口；或 multipart image；生产路径输入必须配置 AOI_ALLOWED_DIRS'}
        async_=endpoint in {'api_v1.ea_bias','api_v1.predict_stage2_folder','api_v1.aoi','api_v1.grading','operations.retry_task','automl_train','automl_train_all','api_v1.close_aoi_stream'}
        success=202 if async_ else 201 if endpoint in {'api_v1.create_task','api_v1.start_aoi_stream','operations.open_product','api_v1.create_qualification','operations.replace','operations.create_review'} else 200
        example={'code':0,'message':'success','data':{'task_id':'0123456789abcdef0123456789abcdef','status':'QUEUED'}} if async_ else {'code':0,'message':'success','data':{'items':[],'total':0,'limit':100,'offset':0}} if endpoint in PAGED else old.get('success_example',{'code':0,'message':'success','data':{}})
        if endpoint=='operations.create_review':example={'code':0,'message':'success','data':{'review_id':'0123456789abcdef0123456789abcdef','suggested_grade':None,'final_grade':None,'reviewer':'operator','decision':'APPROVE'}}
        if endpoint=='api_v1.health':example={'code':0,'message':'success','data':{'web':'ok','stage1':'not_configured','stage2':'model_missing','stage3':'not_configured','database':'sqlite'}}
        if endpoint=='api_v1.ready':example={'code':0,'message':'success','data':{'ready':True}}
        response_media='text/csv' if endpoint in {'operations.export_tasks','operations.export_predictions'} else 'image/png' if endpoint in {'api_v1.aoi_frame_image','api_v1.latest_aoi_frame'} else 'text/html' if endpoint=='static' else 'application/json'
        operation={'operationId':route['operation_id'],'summary':name,'description':purpose,'tags':[category],
            'x-roles':route['auth'],'x-source':route['source']+':'+str(route['line']),'x-asynchronous':async_,
            'x-idempotency':'frame_sequence_product_checksum_view' if endpoint=='api_v1.push_aoi_frame' else 'identity_operation_request_id_payload' if route['method']=='POST' else 'read',
            'parameters':params,'responses':{str(success):{'description':'成功；需满足实际业务条件',
                'content':{response_media:{'schema':{'$ref':'#/components/schemas/'+response_schemas.get(endpoint,'Envelope')} if response_media=='application/json' and path.startswith('/api/v1/') else {'type':'object'} if response_media=='application/json' else {'type':'string'},**({'example':example} if response_media=='application/json' else {})}}}}}
        if route['auth']!=['public']:operation['security']=[{'basicAuth':[]},{'bearerAuth':[]}]
        if body:operation['requestBody']={'required':schema_name not in {'RequestId'},'content':{media:{'schema':body}}}
        if endpoint=='api_v1.aoi':
            multipart=json.loads(json.dumps(body));multipart['properties']['image']={'type':'string','format':'binary'}
            operation['requestBody']['content']['multipart/form-data']={'schema':multipart}
        fail={'code':'MODEL_NOT_CONFIGURED' if endpoint in {'api_v1.aoi','api_v1.grading'} else 'NOT_READY' if 'ready' in endpoint else 'INVALID_ARGUMENT',
              'message':'模型未配置' if endpoint in {'api_v1.aoi','api_v1.grading'} else '请求未满足接口条件','data':{},'request_id':'request-001','timestamp':'2026-10-01T00:00:00Z'}
        failure_status=503 if fail['code'] in {'MODEL_NOT_CONFIGURED','NOT_READY'} else 400
        codes=sorted(set(old.get('codes',[])+[fail['code'],'UNAUTHORIZED','FORBIDDEN','REQUEST_REPLAY_CONFLICT','DISK_CAPACITY_LOW','INTERNAL_ERROR']))
        for status in sorted({failure_status,401,403,404,409,422,429,500,503,507}):
            operation['responses'][str(status)]={'description':{401:'认证失败',403:'角色或资源归属拒绝',404:'资源不存在',409:'幂等冲突、终态、证据状态冲突',422:'证据或输入未满足业务条件',429:'队列/容量已满',503:'未就绪、未配置模型、排空或可重试存储故障',507:'临界磁盘或帧存储容量不足'}.get(status,'请求错误或服务器错误'),
                'content':{'application/json':{'schema':{'$ref':'#/components/schemas/Envelope'},**({'example':fail} if status==failure_status else {})}},
                **({'headers':{'Retry-After':{'schema':{'type':'integer'},'description':'建议等待秒数；重试同一请求/帧，不新造幂等键'}}} if status in {429,503,507} else {})}
        spec['paths'].setdefault(path,{})[route['method'].lower()]=operation
        catalogs.append(dict(route=route,category=category,name=name,purpose=purpose,params=params,body=body,schema_name=schema_name,
            async_=async_,success=success,example=example,fail=fail,failure_status=failure_status,codes=codes,media=media))
    out=ROOT/'docs';out.mkdir(exist_ok=True)
    (out/'openapi.yaml').write_text(yaml.safe_dump(spec,allow_unicode=True,sort_keys=False,width=120),encoding='utf-8')
    (out/'API_ROUTE_INVENTORY.json').write_text(json.dumps({'count':len(routes),'routes':routes},ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 联特科技工业多模态检测系统 HTTP API',f'\n实际 Flask 注册操作数：**{len(routes)}**。API.md 与 OpenAPI 均由实际路由生成；自动 HEAD / OPTIONS 不计入，静态 `/gui/{{filename}}` 计一项。',
        '\n## 概述\n\nStage1 / Stage3 真实模型 NOT CONFIGURED，软件接口保持可接模型状态。Stage2 的模型与数值推理保持原样。所有相对地址以部署 profile 指定的服务为基准。',
        '\n## 鉴权与角色\n\nHTTP Basic 用户名 admin / operator；设备使用 Bearer、X-Liante-Role: device 与 X-Liante-Device-ID。独立设备不能读取或修改其他来源的流、任务、产品、合格证据和导出。管理员配置/排空/训练；操作员可人工登记与复核。production 缺少管理员认证或启用 debug 时拒绝启动。密钥使用部署秘密配置，经受控 HTTPS 或 SSH 隧道传输。',
        '\n## 统一请求、响应与错误\n\n新版响应保留 code / message / data，增加 request_id、timestamp，X-Request-ID 响应头便于跟踪。旧预测与训练成功格式兼容原客户端；认证和准入错误使用统一错误格式。错误不向客户端返回 traceback。',
        '\n```json\n'+json.dumps({'code':'REQUEST_REPLAY_CONFLICT','message':'相同请求编号对应不同参数','data':{},'request_id':'R1','timestamp':'2026-10-01T00:00:00Z'},ensure_ascii=False,indent=2)+'\n```',
        '\n## 幂等与重试\n\n重要 POST 可使用 body request_id 或 X-Request-ID。键由认证身份 + 实际操作路径 + request_id 构成，JSON 键序归一化。相同参数返回原对象及 replayed=true；不同参数返回 409 REQUEST_REPLAY_CONFLICT。帧以 stream + sequence + product + SHA256 + view_type 判重，关闭后仍能重放已 ACK 帧。任务与请求回执在同一 SQLite 事务关联；未知响应时先重试相同键再查询 task_id。容量、磁盘、排空拒收无新任务时可重试，已有终态任务用 retry 新建并保留 retry_of。回执不自动按时间删除，最多 100000，满后拒收，维护期备份归档。',
        '\n## 分页、容量与排空\n\nlimit/offset：默认依端点，通常 100，limit 1..1000，offset 0..10000000；追溯每类最多 200。返回 items/total/limit/offset。Task/来源日志保留顺序见端点；历史按更新时间倒序。Stage1 流和每流帧队列、Stage2/3 工作与等待队列、训练队列均有界；图片、像素、预测行与结果字节也有上限。DISK_WARNING_FREE_RATIO/BYTES 告警；DISK_CRITICAL_FREE_RATIO/BYTES 拒绝新大写入/任务 507 DISK_CAPACITY_LOW，在途继续。429 / 503 / 507 的可重试容量拒绝附 Retry-After。drain 后保留 GET、close、cancel、revoke。JSONL 每任务上限 32 MiB，达到上限报告镜像降级；SQLite 保留每任务最新 20000、全局最新 200000 事件，需要归档以保存长期审计。',
        '\n## Task、持久化与恢复\n\nSQLite 是任务、结果行、结构化日志的权威主存，JSON 为可修复快照，JSONL 为追加审计副本。PENDING → QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED / TIMEOUT / REVIEW_REQUIRED，亦允许提前失败或取消。终态 CAS 禁止迟到回调覆盖。普通中断任务重启标 FAILED 并可创建新任务重试；AOI 按 journal 恢复已确认帧，包括父任务缺失、FAILED 存储故障、PROCESSING 产品；不重新开放历史终态任务。',
        '\n## AOI Stream / Product / Frame / Views\n\nstream_id 兼容现有 task_id；一条流多个产品，各自多帧。视角由流 view_types 配置，required_views 是非空子集，产品可覆盖默认要求。产品 close 不关闭流；流 close 关闭所有未完成产品。状态 RECEIVING / PROCESSING / COMPLETED / FAILED / INCOMPLETE。缺视角或无帧不能 COMPLETED；模型缺失帧 FAILED / MODEL_NOT_CONFIGURED，不伪造 qualified。',
        '\n## Qualification / Grading / Review\n\n人工光电测量必须为有限值、明确单位、有界结构，配方 ID/版本必须与上游结果一致；不会替用户发明合格阈值。需要真实完成的 AOI 产品和 Stage2；独立产品完成即可登记，无需整流结束。操作员/复核身份绑定认证账号，测量时间带时区。证据 ACTIVE / SUPERSEDED / REVOKED / EXPIRED，在提交、执行和结果保存前重查。替换同事务；撤销/过期阻止再次分级。人工复核保留身份、决定、说明、建议/最终等级。真实分级模型缺失且无结果时等级必须 null，复核不是模型合格判定。',
        '\n## Export / Product Trace / Admin / Deployment\n\n任务 CSV、预测 CSV 最多每次 1000 行；证据为单个有界 JSON；产品 trace 各类分别分页，不返回全部预测。system/info 仅管理员，版本/数据库/队列/磁盘信息脱敏。配置 profile 运行 start/stop/drain/status/restart；离线备份恢复与版本完整性检查见 RUNNING_AND_TRAINING_CN.md。',
        '\n## 错误码清单\n\n'+', '.join('`'+code+'`' for code in error_codes()),
        '\n## 接口总览\n\n|模块|Method|Path|用途|角色|异步|幂等|\n|---|---|---|---|---|---|---|']
    for item in catalogs:
        r=item['route'];lines.append('|'+ '|'.join([item['category'],r['method'],'`'+r['path']+'`',item['name'],','.join(r['auth']),str(item['async_']),'帧 journal' if r['endpoint']=='api_v1.push_aoi_frame' else 'request_id' if r['method']=='POST' else '读取'])+'|')
    for item in catalogs:
        r=item['route'];operation=spec['paths'][r['path']][r['method'].lower()]
        lines.extend([f"\n### {item['name']} — `{r['method']} {r['path']}`",'\n用途：'+item['purpose'],
            f"\n角色：{', '.join(r['auth'])}。异步：{item['async_']}。源码：`{r['source']}:{r['line']}`。",'\n|参数|位置|类型 / 范围|必填|说明|\n|---|---|---|---|---|'])
        for p in item['params']:lines.append(f"|{p['name']}|{p['in']}|{json.dumps(p['schema'],ensure_ascii=False)}|{p.get('required',False)}|{p['description']}|")
        schema=components.get(item['schema_name'],{}) if item['schema_name'] else item['body'] or {}
        response_name=response_schemas.get(r['endpoint'],'Envelope')
        lines.append('\n响应字段契约：`'+response_name+'`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。')
        if r['endpoint']=='operations.replace':schema=components['ReplaceQualification']
        for name,field in schema.get('properties',{}).items():
            lines.append(f"|{name}|body|{json.dumps(field,ensure_ascii=False).replace('|',' / ')}|{name in schema.get('required',[])}|见强类型 schema；未知字段拒绝|")
        if not item['params'] and not schema.get('properties'):lines.append('|无|—|—|—|无请求参数|')
        request_example=legacy.get((r['method'],r['path']),{}).get('request_example')
        if r['endpoint']=='operations.create_review':request_example={'product_id':'P-001','qualification_id':None,'stage3_task_id':None,'decision':'APPROVE','comment':'实际人工复核说明；尚无模型等级','final_grade':None,'request_id':'REVIEW-001'}
        if r['endpoint']=='api_v1.create_task':request_example={'stage':'stage2','product_id':'P-001','request_id':'TASK-001'}
        if r['endpoint']=='api_v1.start_aoi_stream':request_example={'source_id':'AOI-001','product_id':'P-001','view_types':['TOP','SIDE'],'required_views':['TOP','SIDE'],'request_id':'STREAM-001'}
        if r['endpoint']=='api_v1.create_qualification':request_example={'product_id':'P-001','aoi_task_id':'0123456789abcdef0123456789abcdef','stage2_task_id':'1123456789abcdef0123456789abcdef','recipe_id':'实际配方编号','recipe_version':'实际版本','optical_features':{'qualified':True,'measurements':[{'name':'power','value':2.1,'unit':'dBm'}]},'electrical_features':{'qualified':True,'measurements':[{'name':'voltage','value':3.3,'unit':'V'}]},'tuning_result':{'status':'complete'},'manual_review':{'decision':'qualified','measured_at':'2026-10-01T08:00:00+08:00','notes':'实际仪器、工单和人工确认说明'}}
        if request_example is None:request_example=f"{r['method']} {r['path']}" if r['method']=='GET' else {name:('R-001' if name=='request_id' else '填写实际'+name) for name in schema.get('required',[])}
        lines.extend(['\n请求示例（参数示意，必须替换真实资源）：\n\n```'+('json' if isinstance(request_example,(dict,list)) else 'text')+'\n'+(json.dumps(request_example,ensure_ascii=False,indent=2) if isinstance(request_example,(dict,list)) else str(request_example))+'\n```',
            f"\n成功响应结构（HTTP {item['success']}，实际模型与证据必须满足条件）：\n\n```json\n{json.dumps(item['example'],ensure_ascii=False,indent=2)}\n```",
            f"\n失败响应示例（HTTP {item['failure_status']}）：\n\n```json\n{json.dumps(item['fail'],ensure_ascii=False,indent=2)}\n```",
            '\n状态码：'+', '.join(operation['responses'])+'。错误码见统一清单及端点实际业务条件。',
            '\n容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。',
            '\n关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。'])
    lines.extend(['\n## 完整实际注册清单\n\n|METHOD|PATH|ENDPOINT|BLUEPRINT|AUTH|SOURCE|\n|---|---|---|---|---|---|'])
    for r in routes:lines.append('|'+ '|'.join([r['method'],r['path'],r['endpoint'],r['blueprint'],','.join(r['auth']),r['source']+':'+str(r['line'])])+'|')
    lines.append('\n## 响应字段契约\n\n以下为数据字段类型及嵌套引用，示例仅说明结构，不代表缺失模型已产出结果。')
    for name in ['TaskRecord','TaskAcknowledgment','StreamSummary','FrameRecord','AOIProduct','QualificationRecord','QualificationEvent','ReviewRecord','TaskEvent','PredictionRow','HealthRecord','SourceRecord','TraceRecord']:
        lines.append('\n### '+name+'\n\n```json\n'+json.dumps(components[name],ensure_ascii=False,indent=2)+'\n```')
    (out/'API.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'actual_routes':len(routes),'api_md':len(catalogs),'openapi_operations':sum(len(v) for v in spec['paths'].values())}))

if __name__=='__main__':main()
