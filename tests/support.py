"""One small public fixture per test process, constructed without private inputs."""
import atexit
from pathlib import Path
import tempfile
from aerial_atlas.demo import create_demo
from aerial_atlas.mosaic import build, current_map

_fixture = None


def fixture():
    global _fixture
    if _fixture is None:
        folder = tempfile.TemporaryDirectory(prefix="atlas synthetic ")
        atexit.register(folder.cleanup)
        project = Path(folder.name)
        source = project / "images/1150427/RGB_png"
        output = project / "generated/1150427"
        create_demo(source)
        report = build(source, output)
        _fixture = dict(source=source, output=output, report=report, **current_map(output))
    return _fixture
