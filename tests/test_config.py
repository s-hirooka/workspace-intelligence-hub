from app.config import MANDATORY_EXCLUDED_DIRS, Settings


def test_comma_separated_excluded_dirs(monkeypatch):
    monkeypatch.setenv("EXCLUDED_DIRS", "bin,obj,node_modules")
    assert Settings(_env_file=None).excluded_dirs == (
        {"bin", "obj", "node_modules"} | MANDATORY_EXCLUDED_DIRS
    )
