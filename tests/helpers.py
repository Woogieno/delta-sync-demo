import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from make_demo_data import build  # noqa: E402


def fresh_demo(n_users=300, n_late=5):
    """테스트마다 새 임시 폴더에 원본/대상 DB를 만든다."""
    tmp = tempfile.TemporaryDirectory()
    out = Path(tmp.name)
    build(out, n_users=n_users, n_late=n_late)
    return tmp, out / "source.db", out / "target.db", out
