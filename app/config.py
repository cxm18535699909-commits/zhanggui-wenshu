"""掌柜问数 · 配置加载"""
import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _abs(p: str) -> str:
    """相对路径统一转为项目根下的绝对路径"""
    path = Path(p)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


class Config:
    def __init__(self, path: str | None = None):
        cfg_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            self._cfg = yaml.safe_load(f)

    def get(self, *keys, default=None):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    # ---- 常用快捷属性 ----
    @property
    def db_backend(self) -> str:
        return self.get("database", "backend", default="sqlite")

    @property
    def sqlite_path(self) -> str:
        return _abs(self.get("database", "sqlite_path", default="data/db/zhanggui.db"))

    @property
    def mysql_conf(self) -> dict:
        return self.get("database", "mysql", default={})

    @property
    def nlu(self) -> dict:
        return self.get("nlu", default={})

    @property
    def row_limit(self) -> int:
        return self.get("answer", "row_limit", default=50)


_config: Config | None = None


def load_config(path: str | None = None) -> Config:
    global _config
    if _config is None or path:
        _config = Config(path)
    return _config
