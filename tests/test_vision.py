import pytest
from prometheus_assistant import cli, vision
from prometheus_assistant.hardware import HardwareProfile, select_model


def test_image_input_rejects_non_image_before_network(tmp_path):
    image=tmp_path/'fake.jpg';image.write_text('not an image')
    with pytest.raises(ValueError,match='PNG and JPEG'):vision.image_content(image)


def test_vision_never_substitutes_a_text_only_model():
    host=HardwareProfile(32,16,'Windows',25)
    assert select_model({'gpt-oss:20b','hermes3:3b'},host,task='vision') is None
    assert select_model({'qwen3-vl:2b'},host,task='vision').model=='qwen3-vl:2b'


def test_see_routes_explicit_image_without_creating_memory(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(cli,'describe_image',lambda *args:calls.append(args) or {'reply':'visible object'})
    db=tmp_path/'memory.db';image=tmp_path/'photo.png'
    assert cli.main(['--memory',str(db),'see',str(image),'What is here?'])==0
    assert calls==[(image,'What is here?','http://127.0.0.1:11434')]
    assert not db.exists()
