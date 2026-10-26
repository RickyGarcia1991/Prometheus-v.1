"""Launch a source checkout with Ubuntu's native crypto and private FIDO2.

Place the checkout at ROOT/app/VERSION and the verified native dependencies
under ROOT/runtime, or pass --runtime-root ROOT/runtime explicitly. No install,
service registration, microphone capture, or elevation occurs in this launcher.
"""
from pathlib import Path
import argparse
import runpy
import sys

def main():
    if sys.platform != "linux":
        raise SystemExit("This launcher requires Ubuntu Linux.")
    source = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runtime-root", type=Path)
    options, arguments = parser.parse_known_args()
    runtime = options.runtime_root
    if runtime is None:
        runtime = next((p/"runtime" for p in source.parents
                        if (p/"runtime/owner-auth").is_dir()), source/"runtime")
    runtime = runtime.resolve(strict=True)
    sys.path[:0] = ["/usr/lib/python3/dist-packages",
                   str(runtime/"owner-auth/usr/lib/python3/dist-packages")]
    import cryptography
    from fido2.server import Fido2Server
    if not Path(cryptography.__file__).resolve().is_relative_to(Path("/usr/lib/python3/dist-packages")):
        raise SystemExit("Ubuntu native cryptography is required.")
    entry = source/"prometheus.py"
    sys.argv = [str(entry), *arguments]
    runpy.run_path(str(entry), run_name="__main__")

if __name__ == "__main__":
    main()
