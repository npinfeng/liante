"""Folder playback uses the production AOI stream journal one frame at a time."""

from io import BytesIO
from pathlib import Path
import time

from PIL import Image


def _image(color: str) -> bytes:
    target = BytesIO()
    Image.new('RGB', (24, 16), color).save(target, format='PNG')
    return target.getvalue()


def test_folder_replay_endpoint_processes_each_image_and_rejects_other_paths(tmp_path, monkeypatch):
    import api_v1
    import backend_server

    allowed = tmp_path / 'data' / 'stage1_input'
    allowed.mkdir(parents=True)
    for index, color in enumerate(('red', 'green', 'blue'), start=1):
        (allowed / f'{index:02d}.png').write_bytes(_image(color))
    (allowed / 'notes.txt').write_text('ignored', encoding='utf-8')
    monkeypatch.setattr(api_v1, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(api_v1, 'AOI_ALLOWED_DIRS', str(allowed))

    app = backend_server.create_app()
    app.testing = True
    seen = []

    class Adapter:
        def predict(self, payload):
            seen.append(payload['frame_sequence'])
            return {'qualified': True, 'defects': []}

    app.extensions['aoi_streams'].adapter = Adapter()
    client = app.test_client()
    forbidden = client.post('/api/v1/aoi/folder-runs', json={
        'directory': str(tmp_path), 'product_id': 'P-1'})
    assert forbidden.status_code == 403

    opened = client.post('/api/v1/aoi/folder-runs', json={
        'directory': 'data/stage1_input', 'product_id': 'P-1',
        'source_id': 'FOLDER-TEST', 'interval_seconds': 0.1})
    assert opened.status_code == 201, opened.json
    task_id = opened.json['data']['task_id']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        task = client.get(f'/api/v1/tasks/{task_id}').json['data']
        if task['status'] in {'COMPLETED', 'FAILED'}:
            break
        time.sleep(0.05)
    assert task['status'] == 'COMPLETED', task
    assert seen == [1, 2, 3]
    assert task['result']['accepted'] == task['result']['processed'] == 3
    frames = client.get(f'/api/v1/aoi/streams/{task_id}/frames').json['data']['items']
    assert [frame['sequence'] for frame in frames] == [1, 2, 3]
    assert all(frame['status'] == 'COMPLETED' for frame in frames)
    assert client.get(f'/api/v1/aoi/streams/{task_id}/frames/2/image').data == (allowed / '02.png').read_bytes()
    folder = client.get(f'/api/v1/aoi/folder-runs/{task_id}').json['data']
    assert folder['submitted'] == 3 and folder['state'] == 'DONE'

    second = client.post('/api/v1/aoi/folder-runs', json={
        'directory': 'data/stage1_input', 'product_id': 'P-2',
        'source_id': 'FOLDER-TEST', 'interval_seconds': 1.0})
    assert second.status_code == 201
    stopped_id = second.json['data']['task_id']
    assert client.post(f'/api/v1/aoi/streams/{stopped_id}/close').status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = client.get(f'/api/v1/aoi/folder-runs/{stopped_id}').json['data']
        if run['state'] == 'STOPPED':
            break
        time.sleep(0.05)
    assert run['state'] == 'STOPPED'
    assert run['submitted'] < 3
    while time.monotonic() < deadline:
        if client.get(f'/api/v1/tasks/{stopped_id}').json['data']['status'] in {'COMPLETED', 'FAILED'}:
            break
        time.sleep(0.05)

    invalid_dir = allowed / 'invalid'
    invalid_dir.mkdir()
    (invalid_dir / 'broken.png').write_bytes(b'not a png')
    invalid = client.post('/api/v1/aoi/folder-runs', json={
        'directory': 'data/stage1_input/invalid', 'product_id': 'P-3',
        'source_id': 'FOLDER-TEST', 'interval_seconds': 0.1})
    assert invalid.status_code == 201
    invalid_id = invalid.json['data']['task_id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        invalid_task = client.get(f'/api/v1/tasks/{invalid_id}').json['data']
        if invalid_task['status'] == 'FAILED':
            break
        time.sleep(0.05)
    assert invalid_task['status'] == 'FAILED'
    assert invalid_task['error_code'] == 'FOLDER_REPLAY_FAILED'
    assert client.get(f'/api/v1/aoi/folder-runs/{invalid_id}').json['data']['state'] == 'FAILED'
    app.extensions['inference'].executor.shutdown(wait=True)
    app.extensions['task_manager'].shutdown(wait=True)
