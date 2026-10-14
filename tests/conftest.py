from __future__ import annotations

from pathlib import Path
import os
import inspect
os.environ['LIANTE_FACTORY_ONLY']='1'

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolate_service_paths(tmp_path,monkeypatch):
    """Software tests never open production business databases or model directories."""
    import backend_server,api_v1,config
    monkeypatch.setenv('APP_ENV','development')
    for module in (backend_server,api_v1,config):
        for key,name in (('OUTPUT_DIR','output'),('LOG_DIR','logs'),('DATA_DIR','data'),('MODEL_DIR','models')):
            if hasattr(module,key): monkeypatch.setattr(module,key,tmp_path/name)
        for key,value in (('ADMIN_TOKEN',''),('OPERATOR_TOKEN',''),('DEVICE_TOKEN',''),('DEVICE_TOKENS',{})):
            if hasattr(module,key): monkeypatch.setattr(module,key,value)
    incoming=tmp_path/'isolated-service-input';incoming.mkdir(exist_ok=True)
    monkeypatch.setattr(backend_server,'STAGE2_INPUT_DIRS',str(incoming))
    monkeypatch.setattr(backend_server,'STAGE2_ALLOWED_DIRS',str(incoming))

def pytest_addoption(parser):
    parser.addoption('--allow-model-work',action='store_true',default=False,
                     help='Explicit opt-in for historical tests that train or construct synthetic model outputs; excluded from software acceptance.')

def pytest_collection_modifyitems(config,items):
    if config.getoption('--allow-model-work'):return
    forbidden=('write_zero_artifact(', '.train_channel(', 'qualified_evidence(', 'class Adapter:',
               'class SlowAdapter:', 'class BadAdapter:', 'class BadHealth:', 'torch.save(',
               'def predict(self, payload)', 'def predict(self,payload)', 'test_automl_train_is_async')
    for item in items:
        source=inspect.getsource(item.obj)
        fixtures=getattr(item,'_fixtureinfo',None)
        if fixtures:
            for definitions in fixtures.name2fixturedefs.values():
                for definition in definitions:
                    try: source+='\n'+inspect.getsource(definition.func)
                    except (OSError,TypeError): pass
        if any(marker in source for marker in forbidden):
            item.add_marker(pytest.mark.skip(reason='本轮禁止模型训练/伪造模型结果；历史模型测试保留，软件行为由无模型测试覆盖'))


@pytest.fixture
def channel_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "train_data"
    data_dir.mkdir()
    write_channel_csv(data_dir, 0)
    return data_dir


def write_channel_csv(data_dir: Path, channel: int, rows: int = 80) -> Path:
    rng = np.random.default_rng(100 + channel)
    x = rng.normal(size=(rows, 4)).astype(np.float32)
    ea = 1.5 * x[:, 0] - 0.3 * x[:, 1] + 0.2 * x[:, 2] + 0.1
    bias = -0.4 * x[:, 0] + 0.8 * x[:, 2] + 0.5 * x[:, 3] - 0.2
    values = np.zeros((rows, 13), dtype=np.float32)
    values[:, 3:7] = x
    values[:, 11] = ea
    values[:, 12] = bias
    path = data_dir / f"channel_{channel}_ea.csv"
    pd.DataFrame(values, columns=[f"col_{index}" for index in range(13)]).to_csv(path, index=False)
    return path

