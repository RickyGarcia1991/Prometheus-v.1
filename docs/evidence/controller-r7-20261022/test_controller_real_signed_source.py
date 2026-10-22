import json,shutil,tempfile
from pathlib import Path
import pytest
import controller_pointer_real_signed_fixture as real

@pytest.mark.parametrize('cache_present',[False,True])
def test_missing_signed_source_refuses_without_mutation(cache_present):
 with tempfile.TemporaryDirectory(prefix='prometheus-full-signed-') as directory:
  root=Path(directory);nonce=real.setup(root);p=real.RealProvider(root,nonce)
  if cache_present:p.stage(p.plan.candidate)
  before_pointer=p.read_pointer();before_floor=p.read_floor()
  source=p.location(p.plan.candidate)
  # Generated temporary package directory only.
  assert source.resolve().is_relative_to(root.resolve())
  shutil.rmtree(source)
  with pytest.raises(ValueError,match='Required signed source missing'):
   real.f.core.execute(p,p.plan,p.key)
  assert p.read_pointer()==before_pointer and p.read_floor()==before_floor
  assert p.read_intent(p.plan.transaction) is None
