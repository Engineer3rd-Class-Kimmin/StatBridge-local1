from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def _load_env() -> None:
    candidates = [
        PACKAGE_ROOT / '.env',
        PROJECT_ROOT / 'kosis_bok_metadata_collector' / '.env',
        Path.cwd() / '.env',
    ]
    for p in candidates:
        if p.exists():
            load_dotenv(p)
            return
    load_dotenv()


def _first_existing(candidates: list[Path]) -> Path:
    for p in candidates:
        if p.exists():
            return p.resolve()
    return candidates[0].resolve()


_load_env()

_default_data = _first_existing([
    PACKAGE_ROOT / 'data' / 'processed',
    PROJECT_ROOT / 'kosis_bok_metadata_collector' / 'data' / 'processed',
])
_default_tables = _first_existing([
    PACKAGE_ROOT / 'data_full' / 'tables',
    PROJECT_ROOT / 'kosis_bok_metadata_collector' / 'data_full' / 'tables',
])


@dataclass(slots=True)
class Settings:
    kosis_api_key: str = os.getenv('KOSIS_API_KEY', '').strip()
    rate_limit_per_minute: int = int(os.getenv('KOSIS_RATE_LIMIT_PER_MINUTE', '50'))
    timeout: int = int(os.getenv('KOSIS_TIMEOUT', '45'))
    data_dir: Path = Path(os.getenv('STATBRIDGE_DATA_DIR', str(_default_data))).resolve()
    tables_dir: Path = Path(os.getenv('STATBRIDGE_TABLES_DIR', str(_default_tables))).resolve()


settings = Settings()
