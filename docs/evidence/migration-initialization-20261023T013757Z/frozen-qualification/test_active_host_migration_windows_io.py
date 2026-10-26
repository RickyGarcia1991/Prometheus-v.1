"""Native IO tests only inside pytest-generated temporary directories."""
import csv,io,os,subprocess
import pytest
from active_host_migration_windows_io import PhysicalFiles,PhysicalRefusal

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Native Windows file handles')

@pytest.fixture
def files(tmp_path):
    sid=next(csv.reader(io.StringIO(subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True,creationflags=subprocess.CREATE_NO_WINDOW))))[1]
    return PhysicalFiles(tmp_path,sid)

def test_physical_create_read_replace_and_create_only(files,tmp_path):
    target=tmp_path/'target';staged=tmp_path/'staged'
    files.create(target,b'old');files.create(staged,b'new')
    with pytest.raises(OSError):files.create(target,b'overwrite')
    files.replace(staged,target,b'old')
    assert files.read(target)==b'new'

@pytest.mark.parametrize('name',['../sibling-forbidden','payload:stream','sub/../../sibling-forbidden'])
def test_escape_or_ads_refuses_before_creation(files,tmp_path,name):
    with pytest.raises(PhysicalRefusal):files.create(tmp_path/name,b'no')
    assert not (tmp_path.parent/'sibling-forbidden').exists()
    assert not (tmp_path/'payload').exists()

def test_wrong_owner_refuses_before_write(tmp_path):
    with pytest.raises(PhysicalRefusal):PhysicalFiles(tmp_path,'S-1-5-99')
    assert not list(tmp_path.iterdir())

def test_expected_old_bytes_required(files,tmp_path):
    files.create(tmp_path/'target',b'foreign');files.create(tmp_path/'staged',b'new')
    with pytest.raises(PhysicalRefusal):files.replace(tmp_path/'staged',tmp_path/'target',b'old')
    assert files.read(tmp_path/'target')==b'foreign' and files.read(tmp_path/'staged')==b'new'
