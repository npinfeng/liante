"""Role matrix used both by authorization and the route inventory."""
PUBLIC={'/health','/api/v1/health','/api/v1/live','/api/v1/ready'}

def roles_for(method,path):
    if path in PUBLIC or path.startswith('/api/v1/ready/'):
        return ['public']
    if path=='/api/v1/system/info' or '/admin/' in path or path.startswith('/automl/') and method=='POST':
        return ['admin']
    if path=='/api/v1/inference/ea-bias/sources' and method=='POST':
        return ['admin','device']
    if path.startswith('/api/v1/aoi/folder-runs'):
        return ['admin','operator']
    if '/qualifications' in path and method=='POST' or path.startswith('/api/v1/reviews'):
        return ['admin','operator']
    if path.startswith(('/api/v1/aoi/streams','/api/v1/tasks','/api/v1/products',
                        '/api/v1/qualifications','/api/v1/inference/ea-bias/sources',
                        '/api/v1/inference/ea-bias/scan','/api/v1/inference/ea-bias/folder',
                        '/api/v1/inference/grading','/api/v1/exports')):
        if method=='POST' and path=='/api/v1/tasks': return ['admin','operator']
        return ['admin','operator','device']
    return ['admin','operator']

def actor_identity():
    from flask import g
    return 'device:'+g.liante_device_id if getattr(g,'liante_device_id',None) else getattr(g,'liante_role','local')
