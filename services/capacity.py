"""Reject new large writes before disk exhaustion while allowing admitted work to finish."""
import os
import shutil
from pathlib import Path

class DiskCapacityError(Exception):
    code='DISK_CAPACITY_LOW'

class CapacityMonitor:
    def __init__(self,paths):
        self.paths=[Path(path) for path in paths]
        self.warning_ratio=float(os.getenv('DISK_WARNING_FREE_RATIO','0.10'))
        self.critical_ratio=float(os.getenv('DISK_CRITICAL_FREE_RATIO','0.03'))
        self.warning_bytes=int(os.getenv('DISK_WARNING_FREE_BYTES',str(2*1024**3)))
        self.critical_bytes=int(os.getenv('DISK_CRITICAL_FREE_BYTES',str(512*1024**2)))
        if not 0 <= self.critical_ratio <= self.warning_ratio <= 1 or not 0 <= self.critical_bytes <= self.warning_bytes:
            raise ValueError('磁盘阈值配置无效')

    def snapshot(self):
        volumes={}
        for path in self.paths:
            candidate=path
            while not candidate.exists(): candidate=candidate.parent
            disk=shutil.disk_usage(candidate)
            volume=candidate.anchor or '/'
            ratio=disk.free/disk.total
            status=('critical' if disk.free<self.critical_bytes or ratio<self.critical_ratio else
                    'warning' if disk.free<self.warning_bytes or ratio<self.warning_ratio else 'ok')
            volumes[volume]=dict(volume=volume,total_bytes=disk.total,free_bytes=disk.free,
                                 free_ratio=round(ratio,4),status=status)
        status='critical' if any(v['status']=='critical' for v in volumes.values()) else 'warning' if any(v['status']=='warning' for v in volumes.values()) else 'ok'
        return dict(status=status,volumes=list(volumes.values()),warning_free_bytes=self.warning_bytes,
                    critical_free_bytes=self.critical_bytes,warning_free_ratio=self.warning_ratio,
                    critical_free_ratio=self.critical_ratio)

    def admit(self):
        if self.snapshot()['status']=='critical':
            raise DiskCapacityError('磁盘容量低于临界阈值，暂不接收新任务或图片；在途任务继续处理')
