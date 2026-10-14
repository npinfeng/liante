"""Server ownership marker used by lifecycle and restore guards."""
import atexit
import json
import os
from pathlib import Path
import psutil

def acquire_server_lock(project,host=None,port=None):
    directory=Path(project)/'.runtime';directory.mkdir(exist_ok=True)
    path=directory/'server.json'
    if path.exists():
        value=json.loads(path.read_text(encoding='utf-8'))
        if psutil.pid_exists(value['pid']): raise RuntimeError('项目已有运行服务，拒绝第二实例')
        path.unlink()
    with path.open('x',encoding='utf-8') as stream:
        json.dump({'pid':os.getpid(),'project':str(Path(project).resolve()),'host':host,'port':port},stream)
    def release():
        try:
            if path.exists() and json.loads(path.read_text(encoding='utf-8'))['pid']==os.getpid(): path.unlink()
        except (OSError,ValueError): pass
    atexit.register(release)
    return path
