from pathlib import Path
from prometheus_assistant.offline_archive import ARCHIVES, inventory, write_manifest

def test_archive_ids_and_hashes():
    assert len({x.id for x in ARCHIVES})==len(ARCHIVES)
    assert all(len(x.sha256)==64 for x in ARCHIVES)
    assert all(x.source_url.endswith(x.filename) for x in ARCHIVES)

def test_inventory_and_manifest(tmp_path:Path):
    rows=inventory(tmp_path)
    assert all(not x["installed"] for x in rows)
    out=write_manifest(tmp_path,tmp_path/"catalog.json")
    assert out.is_file()
    assert '"update_policy": "versioned-replacement"' in out.read_text()
