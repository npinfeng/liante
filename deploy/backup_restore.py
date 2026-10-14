"""Consistent SQLite backups and guarded offline restores; excludes models and raw datasets."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import psutil

DATABASES=('output/tasks/index.sqlite3','output/qualifications.sqlite3','output/stage2_inputs.sqlite3','data/aoi_frames.sqlite3')
FORMAT_VERSION=1

def digest(path):
    checksum=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): checksum.update(chunk)
    return checksum.hexdigest()

def assert_offline(root,host='127.0.0.1',port=8000):
    root=Path(root).resolve()
    lock=root/'.runtime/server.json'
    if lock.exists():
        value=json.loads(lock.read_text(encoding='utf-8'))
        if psutil.pid_exists(value['pid']): raise RuntimeError('目标项目仍在运行，不能恢复数据库')
    with socket.socket() as client:
        client.settimeout(0.25)
        if client.connect_ex((host,port))==0: raise RuntimeError('目标服务端口仍监听，不能恢复数据库')

def backup(root,destination,config_files=(),host='127.0.0.1',port=8000):
    root=Path(root).resolve();destination=Path(destination).resolve()
    assert_offline(root,host,port)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists(): raise FileExistsError('备份目标已存在')
    with tempfile.TemporaryDirectory(prefix='liante-backup-',dir=destination.parent) as work:
        work=Path(work);files={}
        for relative in DATABASES:
            source=root/relative
            if not source.exists(): continue
            target=work/relative;target.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as out:
                src.backup(out)
                if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('源数据库完整性检查失败')
            with closing(sqlite3.connect(target)) as probe: version=probe.execute('PRAGMA user_version').fetchone()[0]
            files[relative]={'sha256':digest(target),'bytes':target.stat().st_size,'schema_version':version}
        if not files: raise ValueError('没有可备份的业务数据库')
        for relative in config_files:
            path=Path(relative)
            if path.is_absolute() or '..' in path.parts or path.parts[0] not in {'deploy','config.py'} or '.secrets' in path.parts:
                raise ValueError('只接受非敏感配置相对路径；凭据由受控密钥存储独立保存')
            source=root/path
            if source.exists():
                target=work/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
                files[path.as_posix()]={'sha256':digest(target),'bytes':target.stat().st_size}
        manifest={'format_version':FORMAT_VERSION,'application_version':'2.0.0','created_at':datetime.now(timezone.utc).isoformat(),
                  'files':files,'excluded':['weights','datasets','credentials'],'consistency':'quiescent/offline'}
        (work/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        temporary=destination.with_suffix(destination.suffix+'.tmp')
        try:
            with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.write(work/'manifest.json','manifest.json')
                for relative in files: archive.write(work/relative,relative)
            os.replace(temporary,destination)
        finally: temporary.unlink(missing_ok=True)
    return manifest

def restore(archive_path,target,host='127.0.0.1',port=8000,replace_existing=False):
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    assert_offline(target,host,port)
    with zipfile.ZipFile(archive_path) as archive,tempfile.TemporaryDirectory(prefix='liante-restore-',dir=target) as work:
        work=Path(work)
        if archive.getinfo('manifest.json').file_size>1024*1024: raise ValueError('备份清单过大')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format_version')!=FORMAT_VERSION or manifest.get('application_version')!='2.0.0': raise ValueError('备份版本不兼容')
        files=manifest.get('files',{})
        if not files or len(files)>32: raise ValueError('备份文件清单无效')
        if set(archive.namelist())!=set(files)|{'manifest.json'}: raise ValueError('备份包含未声明文件')
        size=sum(info['bytes'] for info in files.values())
        if size>32*1024**3 or shutil.disk_usage(target).free<size*2+512*1024**2: raise ValueError('恢复空间不足或备份超过大小上限')
        for relative,info in files.items():
            path=Path(relative)
            if path.is_absolute() or '..' in path.parts or not (relative in DATABASES or relative.startswith('deploy/') or relative=='config.py'):
                raise ValueError('备份路径越界')
            if archive.getinfo(relative).file_size!=info['bytes']: raise ValueError('备份大小不匹配')
            out=work/path;out.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(relative) as source,out.open('wb') as destination: shutil.copyfileobj(source,destination,1024*1024)
            if digest(out)!=info['sha256']: raise ValueError('备份校验和不匹配')
            if relative in DATABASES:
                with closing(sqlite3.connect(out)) as db:
                    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('备份数据库损坏')
                    if db.execute('PRAGMA user_version').fetchone()[0]!=info['schema_version']: raise ValueError('数据库版本不匹配')
            if (target/path).exists() and not replace_existing: raise FileExistsError('目标文件已存在；需要 --replace-existing 并离线')
        assert_offline(target,host,port)
        # Validate every file before any installation. Existing files remain recoverable.
        previous=target/'.restore-backups'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        installed=[];originals=set()
        try:
            for relative in files:
                destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists():
                    saved=previous/relative;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,saved)
                    originals.add(relative)
                    for suffix in ('-wal','-shm'):
                        sidecar=Path(str(destination)+suffix)
                        if sidecar.exists(): shutil.copy2(sidecar,Path(str(saved)+suffix));sidecar.unlink()
                installed.append(relative)
                os.replace(work/relative,destination)
        except Exception:
            for relative in reversed(installed):
                destination=target/relative
                if relative in originals:
                    shutil.copy2(previous/relative,destination)
                    for suffix in ('-wal','-shm'):
                        saved=Path(str(previous/relative)+suffix)
                        if saved.exists(): shutil.copy2(saved,Path(str(destination)+suffix))
                else: destination.unlink(missing_ok=True)
            raise
    return manifest

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['backup','restore'])
    parser.add_argument('--project',type=Path,required=True);parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--replace-existing',action='store_true');args=parser.parse_args()
    # CLI deliberately requires an offline server. The function also supports an externally verified quiescent snapshot.
    assert_offline(args.project,args.host,args.port)
    result=backup(args.project,args.archive,('config.py','deploy/profiles/c209.json'),args.host,args.port) if args.action=='backup' else restore(args.archive,args.project,args.host,args.port,args.replace_existing)
    print(json.dumps({'action':args.action,'files':len(result['files']),'version':result['format_version']}))
if __name__=='__main__': main()
