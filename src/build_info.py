"""Build metadata baked into images; local checkouts expose their current revision."""
import json
from pathlib import Path
import subprocess


def get_build_info():
    path = Path(__file__).with_name('build_info.json')
    if path.is_file():
        value = json.loads(path.read_text(encoding='utf-8'))
        return {'date': value.get('date'), 'commit': (value.get('commit') or '')[:7], 'local': False}
    try:
        commit = subprocess.check_output(['git', 'rev-parse', '--short=7', 'HEAD'],
                                         cwd=path.parent.parent, stderr=subprocess.DEVNULL,
                                         timeout=3, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).decode().strip()
    except (OSError, subprocess.SubprocessError):
        commit = ''
    return {'date': None, 'commit': commit[:7], 'local': True}
