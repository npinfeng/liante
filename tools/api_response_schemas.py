"""Response field contracts used by the runtime-derived documentation generator."""
from copy import deepcopy

def install(components):
    string={'type':'string'};nullable={'type':['string','null']};integer={'type':'integer','minimum':0}
    def ref(name): return {'$ref':'#/components/schemas/'+name}
    def dto(properties): return {'type':'object','properties':properties}
    def array(items): return {'type':'array','items':items}
    def page(items): return dto({'items':array(items),'total':integer,'limit':integer,'offset':integer})
    def envelope(data):
        value=deepcopy(components['Envelope']);value['properties']['data']=data;return value
    components['TaskRecord']=dto({**{name:nullable for name in ['request_id','product_id','stage','stage_name','operation',
        'current_file','source_id','owner_device_id','start_time','update_time','finish_time','error_code','error_message','retry_of']},
        'task_id':string,'status':{'type':'string','enum':['PENDING','QUEUED','RUNNING','CANCEL_REQUESTED','COMPLETED','FAILED','CANCELLED','TIMEOUT','REVIEW_REQUIRED']},
        'progress':{'type':'integer','minimum':0,'maximum':100},'revision':integer,
        'current_channel':{'type':['integer','null'],'minimum':0,'maximum':7},'result':{'type':['object','null']}})
    components['AOIProduct']=dto({**{k:string for k in ['stream_id','product_id','status','created_at','updated_at']},
        **{k:array(string) for k in ['required_views','received_views','missing_views']},
        'closed':{'type':'boolean'},'reason':nullable,'frames':{'type':'object','additionalProperties':integer}})
    qualification=deepcopy(components['QualificationRequest']);qualification.pop('required',None);qualification.pop('additionalProperties',None)
    qualification['properties'].update({**{k:nullable for k in ['qualification_id','created_at','updated_at','expires_at','recorded_by',
        'operator','reviewer','reason','superseded_by','owner_device_id']},'status':{'type':'string','enum':['ACTIVE','SUPERSEDED','REVOKED','EXPIRED']},
        'aoi_result':dto({'qualified':{'type':'boolean'},'frame_count':integer})})
    components['QualificationRecord']=qualification
    review=deepcopy(components['ReviewRequest']);review.pop('required',None);review.pop('additionalProperties',None)
    review['properties'].update({**{k:string for k in ['review_id','reviewer','created_at']},'suggested_grade':{'type':['string','null'],'enum':['A','B','C','D',None]}})
    components['ReviewRecord']=review
    components['TaskEvent']=dto({**{k:nullable for k in ['timestamp','level','request_id','task_id','product_id','stage','stage_name','file_name',
        'file_path','model_name','model_version','recipe_version','operation','status','message','error_code','exception']},
        'channel':{'type':['integer','null']},'progress':{'type':['integer','null']},'duration_ms':{'type':['integer','null']}})
    components['PredictionRow']=dto({'row':integer,'ea':{'type':'number'},'bias':{'type':'number'}})
    components['HealthRecord']=dto({**{k:string for k in ['web','stage1','stage2','stage3','database','gpu','task_store','log_dir','auth']},
        'stage2_channels':array(integer),'alerts':array(string),'draining':{'type':'boolean'},
        **{k:{'type':'object'} for k in ['aoi_streams','inference_queue','training_queue','resources','model_resources']}})
    components['SourceRecord']=dto({**{k:nullable for k in ['source_id','directory','owner_device_id','updated_at']},'revision':integer,'exists':{'type':'boolean'}})
    components['StreamSummary']=dto({**{k:string for k in ['stream_id','source_id']},
        **{k:integer for k in ['accepted','processed','failed','rejected','queue_depth','queue_capacity','last_sequence']},
        **{k:{'type':'boolean'} for k in ['in_flight','accepting','timed_out']},
        'processing_fps':{'type':'number'},'recent_frames':{'type':'array','maxItems':20,'items':{'type':'object'}}})
    components['TaskAcknowledgment']=dto({'task_id':string,'request_id':string,'product_id':string,'status':string,
        'stream':ref('StreamSummary'),'sequence':integer,'view_type':string,'replayed':{'type':'boolean'}})
    components['FrameRecord']=dto({**{k:nullable for k in ['product_id','view_type','status','sha256','model_name','model_version',
        'recipe_version','received_at','processed_at','error_code','error_message']},'sequence':integer,'duration_ms':integer,
        'output':{'type':'object'}})
    components['QualificationEvent']=dto({**{k:nullable for k in ['qualification_id','actor','operation','reason','timestamp',
        'new_qualification_id','review_id','decision']}})
    components['TraceRecord']=dto({'product_id':string,'tasks':page(ref('TaskRecord')),'aoi_products':page(ref('AOIProduct')),
        'qualifications':page(ref('QualificationRecord')),'reviews':page(ref('ReviewRecord')),'limit':integer,'offset':integer,
        'aoi_tasks':array(ref('TaskRecord')),'stage2_results':array(dto({'task_id':string,'status':string,'files_truncated':{'type':'boolean'},
            'files':array(dto({'file_index':integer,'channel':integer,'status':string,'prediction_count':integer,'model_version':nullable,
                'recipe_version':nullable,'predictions_endpoint':nullable,'preview':{'type':'array','maxItems':3,'items':ref('PredictionRow')}}))}))})
    mapping={
        'api_v1.create_task':ref('TaskRecord'),'api_v1.task':ref('TaskRecord'),'operations.task_history':page(ref('TaskRecord')),
        'api_v1.logs':page(ref('TaskEvent')),'api_v1.paged_predictions':page(ref('PredictionRow')),
        'api_v1.health':ref('HealthRecord'),'api_v1.ready':ref('HealthRecord'),
        'operations.stream_product':ref('AOIProduct'),'operations.close_product':ref('AOIProduct'),'operations.open_product':ref('AOIProduct'),
        'operations.stream_products':page(ref('AOIProduct')),
        'api_v1.get_qualification':ref('QualificationRecord'),'api_v1.create_qualification':ref('QualificationRecord'),
        'operations.revoke':ref('QualificationRecord'),'operations.replace':ref('QualificationRecord'),
        'operations.qualification_history':page(ref('QualificationRecord')),'operations.product_qualifications':page(ref('QualificationRecord')),
        'operations.export_qualification':ref('QualificationRecord'),
        'operations.create_review':ref('ReviewRecord'),'operations.review_get':ref('ReviewRecord'),'operations.review_list':page(ref('ReviewRecord')),
        'operations.product_trace':ref('TraceRecord'),'operations.export_trace':ref('TraceRecord'),
        'api_v1.configure_stage2_source':ref('SourceRecord'),
        'api_v1.stage2_sources':dto({'sources':array(ref('SourceRecord')),'model_channels':array(integer)}),
        'api_v1.stage2_source_logs':page(dto({**{k:nullable for k in ['timestamp','source_id','actor','request_id','operation',
            'directory','previous_directory','task_id','message','level']},'http_status':integer,'code':{'type':['string','integer','null']},'revision':integer})),
        'operations.evidence_history':page(ref('QualificationEvent')),
        'api_v1.aoi_frame_records':page(ref('FrameRecord')),
        'api_v1.result':dto({'task_id':string,'status':string,'result':{'type':['object','null']}}),
        'operations.live':dto({'alive':{'type':'boolean'}}),
        'operations.stage_ready':dto({'stage':string,'status':string,'ready':{'type':'boolean'}}),
        'operations.system_info':dto({'version':string,'api_version':string,'environment':string,'started_at':string,'uptime_seconds':{'type':'number'},
            **{k:{'type':'object'} for k in ['databases','disk','queues','training_queue','aoi','snapshots','stages']},'draining':{'type':'boolean'}})}
    for endpoint in ['api_v1.start_aoi_stream','api_v1.push_aoi_frame','api_v1.aoi_stream_status','api_v1.close_aoi_stream',
        'api_v1.ea_bias','api_v1.predict_stage2_folder','api_v1.aoi','api_v1.grading','operations.retry_task']:
        mapping[endpoint]=ref('TaskAcknowledgment')
    result={}
    for endpoint,data in mapping.items():
        name='Response_'+endpoint.replace('.','_');components[name]=envelope(data);result[endpoint]=name
    return result
