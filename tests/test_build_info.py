import json
import subprocess

from src import build_info


def test_image_metadata_is_used_without_git(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, '__file__', str(tmp_path / 'build_info.py'))
    (tmp_path / 'build_info.json').write_text(json.dumps({'date':'2026-10-17T18:00:00+08:00',
                                                       'commit':'abcdef0123456789'}),encoding='utf-8')
    def unexpected(*args, **kwargs):
        raise AssertionError('Images should not need git')
    monkeypatch.setattr(build_info.subprocess, 'check_output', unexpected)
    assert build_info.get_build_info() == {'date':'2026-10-17T18:00:00+08:00','commit':'abcdef0','local':False}


def test_local_build_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, '__file__', str(tmp_path / 'build_info.py'))
    monkeypatch.setattr(build_info.subprocess, 'check_output', lambda *args, **kwargs:b'123abcd\n')
    assert build_info.get_build_info() == {'date':None,'commit':'123abcd','local':True}


def test_local_without_git(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, '__file__', str(tmp_path / 'build_info.py'))
    def missing(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(build_info.subprocess, 'check_output', missing)
    assert build_info.get_build_info() == {'date':None,'commit':'','local':True}
