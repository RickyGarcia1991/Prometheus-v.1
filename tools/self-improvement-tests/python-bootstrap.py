"""Trusted isolated pytest bootstrap; invoked by the fixed host runner only."""
import os
from pathlib import Path
import sys

bundle, packages, workspace = (Path(value).resolve() for value in sys.argv[1:4])
sys.path[:0] = [str(bundle), str(packages), str(workspace / 'src')]
import sitecustomize  # noqa: E402 - explicit: initialization errors must fail closed
import pytest  # noqa: E402

if not Path(pytest.__file__).resolve().is_relative_to(packages):
    raise RuntimeError('pytest was not loaded from the verified offline bundle')
if __name__ == '__main__':
    # Windows multiprocessing imports this file as __mp_main__. It must only
    # establish trusted imports/guards, never recursively launch the suite.
    raise SystemExit(pytest.main(sys.argv[4:]))
