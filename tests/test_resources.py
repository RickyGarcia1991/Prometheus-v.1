from pathlib import Path
from prometheus_assistant.resources import inventory, write_catalog

def test_inventory_detects_installed_resource(tmp_path):
    p=tmp_path/"Knowledge"/"Kiwix";p.mkdir(parents=True)
    (p/"wikipedia_en_top_mini_2026-09.zim").write_bytes(b"zim")
    rows={r["id"]:r for r in inventory(str(tmp_path))}
    assert rows["wikipedia-top"]["installed"] is True
    assert rows["wikipedia-top"]["size_bytes"] == 3
    assert rows["openstax"]["installed"] is False

def test_catalog_is_written(tmp_path):
    out=write_catalog(tmp_path/"catalog.json",str(tmp_path))
    assert out.exists()
    assert '"schema": 1' in out.read_text(encoding="utf-8")


def test_current_inventory_does_not_advertise_absent_legacy_downloads(tmp_path):
    folder = tmp_path/'Knowledge/Kiwix/wikipedia'
    folder.mkdir(parents=True)
    (folder/'wikipedia_en_all_nopic_2026-06.zim').write_bytes(b'zim')
    rows = {row['id']: row for row in inventory(str(tmp_path))}
    assert rows['wikipedia-en-all-nopic']['installed'] is True
    assert 'wikipedia-top' not in rows
    assert 'wikipedia-astronomy' not in rows


def test_missing_current_archive_remains_visible(tmp_path):
    rows = {row['id']: row for row in inventory(str(tmp_path))}
    assert rows['wikipedia-en-all-nopic']['installed'] is False
