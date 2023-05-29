from pathlib import Path


def get_abs_path(p: str) -> str:
    return str(Path(p).resolve())


class AbsolutePath:
    def __init__(self, path: str):
        self.path = path

    def __call__(self) -> str:
        return get_abs_path(self.path)
