import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.run_cpu_trial import ARTIFACTS, main


def prepare(tmp_path):
    folder = tmp_path / 'export'
    (folder / 'ssh').mkdir(parents=True)
    for name in ('spk_cpu_trial_rsa', 'known_hosts'):
        (folder / 'ssh' / name).write_text('fixture')
    records = {}
    for name in ARTIFACTS:
        (folder / name).write_bytes(b'fixture')
        records[name] = {'bytes': 7, 'sha256': hashlib.sha256(b'fixture').hexdigest()}
    (folder / 'manifest.json').write_text(json.dumps({'files': records}))
    repo = tmp_path / 'repo'
    (repo / 'deploy' / 'cpu-trial').mkdir(parents=True)
    (repo / 'deploy' / 'cpu-trial' / 'bootstrap.py').write_text('# fixture')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'host': '192.0.2.1', 'server_id': '123', 'export_folder': str(folder), 'repository': str(repo)}))
    return config, folder


def test_transfer_uploads_only_artifacts_and_never_private_key(tmp_path):
    config, folder = prepare(tmp_path)
    with patch('scripts.run_cpu_trial.subprocess.check_output', side_effect=['cv123\n', '{"db":true,"llm":true}']), patch('scripts.run_cpu_trial.subprocess.run') as execute:
        main(config)
    scp = [call.args[0] for call in execute.call_args_list if call.args[0][0] == 'scp']
    assert len(scp) == 1
    assert scp[0][-7:-1] == [*ARTIFACTS, 'manifest.json', 'bootstrap.py']
    assert scp[0][-1] == 'root@192.0.2.1:/opt/spk-cpu-trial/transfer/'
    assert json.loads((folder / 'transfer-result.json').read_text())['status'] == 'ready'


def test_wrong_server_stops_before_upload_or_mutation(tmp_path):
    config, folder = prepare(tmp_path)
    with patch('scripts.run_cpu_trial.subprocess.check_output', return_value='another-server\n'), patch('scripts.run_cpu_trial.subprocess.run') as execute:
        with pytest.raises(RuntimeError, match='Unexpected server'):
            main(config)
    execute.assert_not_called()
    assert json.loads((folder / 'transfer-result.json').read_text())['status'] == 'failed'
