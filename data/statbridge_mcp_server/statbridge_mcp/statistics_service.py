from __future__ import annotations

from glob import glob
from pathlib import Path
from typing import Any
import re

import pandas as pd

from .config import settings
from .kosis_client import KosisApiError, KosisClient
from .metadata_store import (
    MetadataStore,
    normalize_frequency,
    normalize_period,
)


# ============================================================
# 공통 유틸
# ============================================================


def _norm_map(columns: list[str]) -> dict[str, str]:
    return {
        c.replace("_", "").lower(): c
        for c in columns
    }


def _col(
    df: pd.DataFrame,
    *names: str,
) -> str | None:
    """
    대소문자/underscore 차이를 무시하고
    실제 DataFrame 컬럼명을 찾는다.
    """

    mapping = _norm_map(
        list(df.columns)
    )

    for name in names:
        key = (
            name
            .replace("_", "")
            .lower()
        )

        if key in mapping:
            return mapping[key]

    return None


def _local_frequency_values(
    frequency: str | None,
) -> set[str]:
    """
    StatBridge canonical frequency를
    실제 KOSIS CSV의 PRD_SE 값 후보로 변환한다.

    특히 KOSIS에서는 연간이
    Y 또는 A로 존재할 수 있다.
    """

    if not frequency:
        return set()

    freq = normalize_frequency(
        frequency
    )

    mapping = {
        "D": {"D"},
        "M": {"M"},
        "Q": {"Q"},
        "S": {"S", "H"},
        "Y": {"Y", "A"},
        "IR": {"IR"},
    }

    return mapping.get(
        freq,
        {freq},
    )


class StatisticsService:

    def __init__(
        self,
        store: MetadataStore | None = None,
        client: KosisClient | None = None,
    ) -> None:

        self.store = (
            store
            or MetadataStore()
        )

        self.client = (
            client
            or KosisClient()
        )

        self.tables_dir = (
            settings.tables_dir
        )

    # ========================================================
    # SEARCH
    # ========================================================

    def search_tables(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:

        from .search_engine import (
            SearchEngine,
        )

        return SearchEngine(
            self.store
        ).search(
            query=query,
            top_k=top_k,
        )

    # ========================================================
    # LIVE PERIOD METADATA
    # ========================================================

    def _live_period_metadata(
        self,
        table_id: str,
    ) -> dict[str, Any]:

        meta = self.store.get(
            table_id
        )

        if not meta:
            raise ValueError(
                f"존재하지 않는 table_id: "
                f"{table_id}"
            )

        rows = (
            self.client.get_prd_meta(
                meta.org_id or "301",
                table_id,
            )
        )

        frequencies: list[str] = []

        periods_by_freq: dict[
            str,
            list[str],
        ] = {}

        current_freq = ""

        for row in rows:

            raw_freq = ""
            raw_period = ""

            for key, value in (
                row.items()
            ):

                normalized_key = (
                    key
                    .replace("_", "")
                    .lower()
                )

                if (
                    normalized_key
                    == "prdse"
                    and value
                    not in ("", None)
                ):
                    raw_freq = str(
                        value
                    ).strip()

                elif (
                    normalized_key
                    == "prdde"
                    and value
                    not in ("", None)
                ):
                    raw_period = str(
                        value
                    ).strip()

            if raw_freq:

                current_freq = (
                    normalize_frequency(
                        raw_freq
                    )
                )

                if (
                    current_freq
                    and current_freq
                    not in frequencies
                ):
                    frequencies.append(
                        current_freq
                    )

                periods_by_freq.setdefault(
                    current_freq,
                    [],
                )

            if (
                raw_period
                and current_freq
            ):

                normalized_period = (
                    normalize_period(
                        current_freq,
                        raw_period,
                    )
                )

                if normalized_period:
                    periods_by_freq.setdefault(
                        current_freq,
                        [],
                    ).append(
                        normalized_period
                    )

        # 중복 제거 및 정렬
        for freq in list(
            periods_by_freq.keys()
        ):
            periods_by_freq[freq] = (
                sorted(
                    set(
                        periods_by_freq[
                            freq
                        ]
                    )
                )
            )

        flattened = [
            p
            for values
            in periods_by_freq.values()
            for p in values
            if p
        ]

        canonical_periods: list[
            dict[str, Any]
        ] = []

        for freq in frequencies:

            values = (
                periods_by_freq.get(
                    freq,
                    [],
                )
            )

            if not values:
                continue

            canonical_periods.append(
                {
                    "frequency":
                        freq,

                    "start_period":
                        values[0],

                    "end_period":
                        values[-1],

                    "start_period_raw":
                        None,

                    "end_period_raw":
                        None,
                }
            )

        return {
            "frequencies":
                frequencies,

            "periods":
                canonical_periods,

            # 내부 호환용
            "periods_by_freq":
                periods_by_freq,

            "start_period":
                (
                    min(flattened)
                    if flattened
                    else None
                ),

            "end_period":
                (
                    max(flattened)
                    if flattened
                    else None
                ),
        }

    # ========================================================
    # GET TABLE METADATA
    # ========================================================

    def get_table_metadata(
        self,
        table_id: str,
        live_period_fallback: bool = True,
    ) -> dict[str, Any]:

        """
        StatBridge Canonical Metadata Schema v1.0
        """

        meta = self.store.get(
            table_id
        )

        if not meta:
            raise ValueError(
                f"존재하지 않는 table_id: "
                f"{table_id}"
            )

        result = (
            meta.canonical_dict()
        )

        needs_fallback = (
            not result.get(
                "frequencies"
            )
            or not result.get(
                "periods"
            )
        )

        if (
            live_period_fallback
            and needs_fallback
        ):

            try:

                live = (
                    self._live_period_metadata(
                        table_id
                    )
                )

                if (
                    not result.get(
                        "frequencies"
                    )
                    and live.get(
                        "frequencies"
                    )
                ):
                    result[
                        "frequencies"
                    ] = live[
                        "frequencies"
                    ]

                if (
                    not result.get(
                        "periods"
                    )
                    and live.get(
                        "periods"
                    )
                ):
                    result[
                        "periods"
                    ] = live[
                        "periods"
                    ]

                result[
                    "period_source"
                ] = (
                    "kosis_live_prd_fallback"
                )

            except Exception as e:

                result[
                    "period_fallback_error"
                ] = str(e)

        return result

    # ========================================================
    # LOCAL CSV
    # ========================================================

    def _find_local_table_csv(
        self,
        table_id: str,
    ) -> Path | None:

        patterns = [
            str(
                self.tables_dir
                / f"{table_id}__*.csv"
            ),
            str(
                self.tables_dir
                / f"{table_id}.csv"
            ),
        ]

        for pattern in patterns:

            matches = glob(
                pattern
            )

            if matches:
                return Path(
                    matches[0]
                ).resolve()

        return None

    def _classification_csv_column(
        self,
        df: pd.DataFrame,
        obj_key: str,
    ) -> str | None:
        """
        MCP 파라미터:
            objL1
            objL2
            objL3

        실제 KOSIS 데이터 컬럼:
            C1
            C2
            C3

        로 변환한다.
        """

        key = str(
            obj_key
        ).strip()

        match = re.fullmatch(
            r"objL(\d+)",
            key,
            flags=re.IGNORECASE,
        )

        if match:

            dimension_index = int(
                match.group(1)
            )

            return _col(
                df,
                f"C{dimension_index}",
            )

        # 혹시 호출자가 C1을 직접 넘긴 경우도 지원
        return _col(
            df,
            key,
        )

    def _filter_local_data(
        self,
        table_id: str,
        item_id: str,
        classifications: (
            dict[str, str]
            | None
        ),
        frequency: str | None,
        start_period: str | None,
        end_period: str | None,
    ) -> list[dict[str, Any]]:

        path = (
            self._find_local_table_csv(
                table_id
            )
        )

        if (
            not path
            or not path.exists()
        ):
            return []

        df = pd.read_csv(
            path,
            dtype=str,
        ).fillna("")

        tbl_col = _col(
            df,
            "TBL_ID",
            "tblId",
        )

        itm_col = _col(
            df,
            "ITM_ID",
            "itmId",
        )

        prd_se_col = _col(
            df,
            "PRD_SE",
            "prdSe",
        )

        prd_de_col = _col(
            df,
            "PRD_DE",
            "prdDe",
        )

        # ----------------------------------------------------
        # Table ID
        # ----------------------------------------------------

        if tbl_col:
            df = df[
                df[tbl_col]
                == table_id
            ]

        # ----------------------------------------------------
        # Item
        # ----------------------------------------------------

        if (
            itm_col
            and item_id
            and item_id != "ALL"
        ):
            df = df[
                df[itm_col]
                == item_id
            ]

        # ----------------------------------------------------
        # Frequency
        # Y ↔ A 같은 KOSIS 차이까지 처리
        # ----------------------------------------------------

        if (
            prd_se_col
            and frequency
        ):

            allowed_freqs = (
                _local_frequency_values(
                    frequency
                )
            )

            if allowed_freqs:

                df = df[
                    df[prd_se_col]
                    .isin(
                        allowed_freqs
                    )
                ]

        # ----------------------------------------------------
        # Period
        # ----------------------------------------------------

        if (
            prd_de_col
            and start_period
        ):
            df = df[
                df[prd_de_col]
                >= str(
                    start_period
                )
            ]

        if (
            prd_de_col
            and end_period
        ):
            df = df[
                df[prd_de_col]
                <= str(
                    end_period
                )
            ]

        # ----------------------------------------------------
        # Classification
        #
        # 핵심 수정:
        # objL1 → C1
        # objL2 → C2
        # objL3 → C3
        # ...
        # ----------------------------------------------------

        for (
            obj_key,
            obj_val,
        ) in (
            classifications
            or {}
        ).items():

            if (
                not obj_val
                or obj_val == "ALL"
            ):
                continue

            csv_col = (
                self
                ._classification_csv_column(
                    df,
                    obj_key,
                )
            )

            if not csv_col:
                continue

            df = df[
                df[csv_col]
                == str(
                    obj_val
                )
            ]

        return df.to_dict(
            orient="records"
        )

    # ========================================================
    # CLASSIFICATION
    # ========================================================

    def _classification_depth(
        self,
        table_id: str,
    ) -> int:

        meta = self.store.get(
            table_id
        )

        if (
            not meta
            or not meta.dimensions
        ):
            return 1

        indexes: list[int] = []

        for dim in meta.dimensions:

            try:
                index = int(
                    dim.get(
                        "dimension_index",
                        0,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            if index > 0:
                indexes.append(
                    index
                )

        return (
            max(indexes)
            if indexes
            else 1
        )

    def _first_real_classifications(
        self,
        table_id: str,
    ) -> dict[str, str]:

        meta = self.store.get(
            table_id
        )

        if (
            not meta
            or not meta.dimensions
        ):
            return {
                "objL1": "ALL"
            }

        out: dict[
            str,
            str,
        ] = {}

        dimensions = sorted(
            meta.dimensions,
            key=lambda d: int(
                d.get(
                    "dimension_index",
                    999,
                )
            ),
        )

        for dim in dimensions:

            try:
                index = int(
                    dim.get(
                        "dimension_index",
                        0,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            if index <= 0:
                continue

            values = (
                dim.get(
                    "values"
                )
                or []
            )

            first_id = ""

            for value in values:

                class_id = str(
                    value.get(
                        "class_id",
                        "",
                    )
                ).strip()

                if class_id:
                    first_id = (
                        class_id
                    )
                    break

            out[
                f"objL{index}"
            ] = (
                first_id
                or "ALL"
            )

        return (
            out
            or {
                "objL1": "ALL"
            }
        )

    # ========================================================
    # API ATTEMPTS / FALLBACK
    # ========================================================

    def _api_attempts(
        self,
        table_id: str,
        item_id: str,
        classifications: (
            dict[str, str]
            | None
        ),
        frequency: str,
        start_period: str,
        end_period: str,
    ) -> list[
        tuple[
            str,
            dict[str, Any],
        ]
    ]:

        meta = self.store.get(
            table_id
        )

        if not meta:
            raise ValueError(
                f"존재하지 않는 table_id: "
                f"{table_id}"
            )

        org_id = (
            meta.org_id
            or "301"
        )

        objs = {
            k: v
            for k, v
            in (
                classifications
                or {}
            ).items()
            if v not in (
                "",
                None,
            )
        }

        depth = (
            self._classification_depth(
                table_id
            )
        )

        all_by_depth = {
            f"objL{i}": "ALL"
            for i
            in range(
                1,
                depth + 1,
            )
        }

        first_real = (
            self
            ._first_real_classifications(
                table_id
            )
        )

        attempts_raw = [
            (
                "requested",
                item_id,
                objs,
            ),
            (
                "requested_item_all_classes",
                item_id,
                all_by_depth,
            ),
            (
                "item_all_requested_classes",
                "ALL",
                objs,
            ),
            (
                "item_all_all_classes",
                "ALL",
                all_by_depth,
            ),
            (
                "requested_item_first_real_classes",
                item_id,
                first_real,
            ),
            (
                "item_all_first_real_classes",
                "ALL",
                first_real,
            ),
        ]

        # 실제 item id fallback
        real_item_ids = [
            i.get(
                "item_id",
                "",
            )
            for i
            in meta.items
            if i.get(
                "item_id"
            )
        ]

        for rid in (
            real_item_ids[:5]
        ):

            attempts_raw.extend(
                [
                    (
                        "real_item_all_classes",
                        rid,
                        all_by_depth,
                    ),
                    (
                        "real_item_first_real_classes",
                        rid,
                        first_real,
                    ),
                ]
            )

        # 중복 제거
        seen: set[
            tuple[
                str,
                tuple,
            ]
        ] = set()

        attempts: list[
            tuple[
                str,
                dict[str, Any],
            ]
        ] = []

        for (
            name,
            iid,
            cls,
        ) in attempts_raw:

            key = (
                iid,
                tuple(
                    sorted(
                        cls.items()
                    )
                ),
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            attempts.append(
                (
                    name,
                    {
                        "org_id":
                            org_id,

                        "table_id":
                            table_id,

                        "item_id":
                            iid,

                        "frequency":
                            frequency,

                        "start_period":
                            start_period,

                        "end_period":
                            end_period,

                        "classifications":
                            cls,
                    },
                )
            )

        return attempts

    # ========================================================
    # GET STATISTICS
    # ========================================================

    def get_statistics(
        self,
        table_id: str,
        item_id: str = "ALL",
        classifications: (
            dict[str, str]
            | None
        ) = None,
        frequency: str | None = None,
        start_period: str | None = None,
        end_period: str | None = None,
        prefer_local: bool = True,
        allow_fallback: bool = True,
    ) -> dict[str, Any]:

        meta = self.store.get(
            table_id
        )

        if not meta:
            raise ValueError(
                f"존재하지 않는 table_id: "
                f"{table_id}"
            )

        # ----------------------------------------------------
        # KOSIS OpenAPI 예외 테이블
        # ----------------------------------------------------

        if (
            meta.api_status
            != "SUPPORTED"
        ):

            return {
                "source":
                    "none",

                "status":
                    meta.api_status,

                "table_id":
                    table_id,

                "table_name":
                    meta.table_name,

                "used_params": {
                    "item_id":
                        item_id,

                    "classifications":
                        (
                            classifications
                            or {}
                        ),

                    "frequency":
                        frequency,

                    "start_period":
                        start_period,

                    "end_period":
                        end_period,
                },

                "row_count":
                    0,

                "rows":
                    [],

                "errors": [
                    {
                        "code":
                            (
                                "KOSIS_"
                                "OPENAPI_"
                                "EXCEPTION"
                            ),

                        "message":
                            (
                                "KOSIS 목록에는 "
                                "존재하지만 일반 "
                                "Parameter OpenAPI "
                                "방식으로 조회할 수 "
                                "없는 예외 통계표입니다."
                            ),
                    }
                ],
            }

        # ----------------------------------------------------
        # Metadata
        # ----------------------------------------------------

        effective_meta = (
            self.get_table_metadata(
                table_id,
                # Explicit UI ranges already provide the period. A live PRD metadata
                # round-trip here duplicated every chart request and could take 45 s.
                live_period_fallback=not bool(start_period and end_period),
            )
        )

        frequencies = (
            effective_meta.get(
                "frequencies"
            )
            or []
        )

        frequency = (
            normalize_frequency(
                frequency
            )
            if frequency
            else (
                frequencies[0]
                if frequencies
                else "Y"
            )
        )

        # ----------------------------------------------------
        # Canonical periods
        # ----------------------------------------------------

        period_rows = [
            p
            for p
            in (
                effective_meta.get(
                    "periods"
                )
                or []
            )
            if p.get(
                "frequency"
            )
            == frequency
        ]

        if not period_rows:
            period_rows = (
                effective_meta.get(
                    "periods"
                )
                or []
            )

        available_starts = [
            str(
                p.get(
                    "start_period",
                    "",
                )
            ).strip()
            for p
            in period_rows
            if str(
                p.get(
                    "start_period",
                    "",
                )
            ).strip()
        ]

        available_ends = [
            str(
                p.get(
                    "end_period",
                    "",
                )
            ).strip()
            for p
            in period_rows
            if str(
                p.get(
                    "end_period",
                    "",
                )
            ).strip()
        ]

        start_period = (
            str(
                start_period
            )
            if start_period
            else (
                min(
                    available_starts
                )
                if available_starts
                else ""
            )
        )

        end_period = (
            str(
                end_period
            )
            if end_period
            else (
                max(
                    available_ends
                )
                if available_ends
                else ""
            )
        )

        # ----------------------------------------------------
        # Classification default
        # ----------------------------------------------------

        if not classifications:

            classifications = {
                f"objL{i}": "ALL"
                for i
                in range(
                    1,
                    (
                        self
                        ._classification_depth(
                            table_id
                        )
                        + 1
                    ),
                )
            }

        # ----------------------------------------------------
        # Local CSV first
        # ----------------------------------------------------

        if prefer_local:

            local_rows = (
                self._filter_local_data(
                    table_id=table_id,
                    item_id=item_id,
                    classifications=(
                        classifications
                    ),
                    frequency=frequency,
                    start_period=(
                        start_period
                    ),
                    end_period=(
                        end_period
                    ),
                )
            )

            if local_rows:

                return {
                    "source":
                        "local_csv",

                    "status":
                        "success",

                    "table_id":
                        table_id,

                    "table_name":
                        meta.table_name,

                    "used_params": {
                        "item_id":
                            item_id,

                        "classifications":
                            classifications,

                        "frequency":
                            frequency,

                        "start_period":
                            start_period,

                        "end_period":
                            end_period,
                    },

                    "row_count":
                        len(
                            local_rows
                        ),

                    "rows":
                        local_rows,
                }

        # ----------------------------------------------------
        # KOSIS OpenAPI
        # ----------------------------------------------------

        errors: list[
            dict[str, Any]
        ] = []

        attempts = (
            self._api_attempts(
                table_id=table_id,
                item_id=item_id,
                classifications=(
                    classifications
                ),
                frequency=frequency,
                start_period=(
                    start_period
                ),
                end_period=(
                    end_period
                ),
            )
        )
        if not allow_fallback:
            attempts = attempts[:1]

        for (
            attempt_name,
            params,
        ) in attempts:

            try:

                rows = (
                    self.client
                    .get_statistics(
                        org_id=(
                            params[
                                "org_id"
                            ]
                        ),
                        table_id=(
                            params[
                                "table_id"
                            ]
                        ),
                        item_id=(
                            params[
                                "item_id"
                            ]
                        ),
                        frequency=(
                            params[
                                "frequency"
                            ]
                        ),
                        start_period=(
                            params[
                                "start_period"
                            ]
                        ),
                        end_period=(
                            params[
                                "end_period"
                            ]
                        ),
                        classifications=(
                            params[
                                "classifications"
                            ]
                        ),
                    )
                )

                if rows:

                    return {
                        "source":
                            "kosis_api",

                        "status":
                            "success",

                        "attempt":
                            attempt_name,

                        "table_id":
                            table_id,

                        "table_name":
                            meta.table_name,

                        "used_params": {
                            "item_id":
                                params[
                                    "item_id"
                                ],

                            "classifications":
                                params[
                                    "classifications"
                                ],

                            "frequency":
                                params[
                                    "frequency"
                                ],

                            "start_period":
                                params[
                                    "start_period"
                                ],

                            "end_period":
                                params[
                                    "end_period"
                                ],
                        },

                        "row_count":
                            len(rows),

                        "rows":
                            rows,
                    }

            except KosisApiError as e:

                errors.append(
                    {
                        "attempt":
                            attempt_name,

                        "error":
                            (
                                f"{e.code}:"
                                f"{e.msg}"
                            ),
                    }
                )

            except Exception as e:

                errors.append(
                    {
                        "attempt":
                            attempt_name,

                        "error":
                            str(e),
                    }
                )

        return {
            "source":
                "none",

            "status":
                "failed",

            "table_id":
                table_id,

            "table_name":
                meta.table_name,

            "used_params": {
                "item_id":
                    item_id,

                "classifications":
                    classifications,

                "frequency":
                    frequency,

                "start_period":
                    start_period,

                "end_period":
                    end_period,
            },

            "row_count":
                0,

            "rows":
                [],

            "errors":
                errors,
        }

    # ========================================================
    # VALIDATE TABLES BATCH
    # ========================================================

    def validate_tables_batch(
        self,
        offset: int = 0,
        limit: int = 20,
        prefer_local: bool = False,
        include_success_results: bool = True,
    ) -> dict[str, Any]:

        """
        Inspector timeout을 피하기 위해
        작은 batch 단위로 검증한다.
        """

        all_metas = (
            self.store
            .available_supported_tables()
        )

        total_available = len(
            all_metas
        )

        offset = max(
            0,
            int(offset),
        )

        limit = max(
            1,
            int(limit),
        )

        metas = all_metas[
            offset:
            offset + limit
        ]

        results: list[
            dict[str, Any]
        ] = []

        success = 0
        fallback_success = 0
        failed = 0

        for meta in metas:

            try:

                md = (
                    self
                    .get_table_metadata(
                        meta.table_id,
                        live_period_fallback=True,
                    )
                )

                freqs = (
                    md.get(
                        "frequencies"
                    )
                    or []
                )

                freq = (
                    freqs[0]
                    if freqs
                    else "Y"
                )

                # canonical period
                period_rows = [
                    p
                    for p
                    in (
                        md.get(
                            "periods"
                        )
                        or []
                    )
                    if p.get(
                        "frequency"
                    )
                    == freq
                ]

                end_periods = sorted(
                    {
                        str(
                            p.get(
                                "end_period",
                                "",
                            )
                        ).strip()

                        for p
                        in period_rows

                        if str(
                            p.get(
                                "end_period",
                                "",
                            )
                        ).strip()
                    }
                )

                latest = (
                    end_periods[-1]
                    if end_periods
                    else None
                )

                if not latest:

                    failed += 1

                    results.append(
                        {
                            "table_id":
                                meta.table_id,

                            "table_name":
                                meta.table_name,

                            "status":
                                "failed",

                            "reason":
                                "period_not_found",
                        }
                    )

                    continue

                item_id = (
                    meta.items[0][
                        "item_id"
                    ]
                    if meta.items
                    else "ALL"
                )

                classes = (
                    self
                    ._first_real_classifications(
                        meta.table_id
                    )
                )

                res = (
                    self.get_statistics(
                        table_id=(
                            meta.table_id
                        ),
                        item_id=(
                            item_id
                        ),
                        classifications=(
                            classes
                        ),
                        frequency=(
                            freq
                        ),
                        start_period=(
                            latest
                        ),
                        end_period=(
                            latest
                        ),
                        prefer_local=(
                            prefer_local
                        ),
                    )
                )

                if (
                    res.get(
                        "row_count",
                        0,
                    )
                    > 0
                ):

                    success += 1

                    used_attempt = (
                        res.get(
                            "attempt",
                            "local_csv",
                        )
                    )

                    if (
                        used_attempt
                        not in (
                            None,
                            "requested",
                            "local_csv",
                        )
                    ):
                        fallback_success += 1

                    if (
                        include_success_results
                    ):

                        results.append(
                            {
                                "table_id":
                                    meta.table_id,

                                "table_name":
                                    meta.table_name,

                                "status":
                                    "success",

                                "attempt":
                                    used_attempt,

                                "frequency":
                                    freq,

                                "period":
                                    latest,

                                "row_count":
                                    res.get(
                                        "row_count",
                                        0,
                                    ),
                            }
                        )

                else:

                    failed += 1

                    results.append(
                        {
                            "table_id":
                                meta.table_id,

                            "table_name":
                                meta.table_name,

                            "status":
                                "failed",

                            "frequency":
                                freq,

                            "period":
                                latest,

                            "errors":
                                res.get(
                                    "errors",
                                    [],
                                ),
                        }
                    )

            except Exception as e:

                failed += 1

                results.append(
                    {
                        "table_id":
                            meta.table_id,

                        "table_name":
                            meta.table_name,

                        "status":
                            "failed",

                        "reason":
                            str(e),
                    }
                )

        next_offset = (
            offset
            + len(metas)
        )

        has_more = (
            next_offset
            < total_available
        )

        return {
            "total_available":
                total_available,

            "offset":
                offset,

            "limit":
                limit,

            "tested_in_batch":
                len(metas),

            "success":
                success,

            "fallback_success":
                fallback_success,

            "failed":
                failed,

            "success_rate":
                (
                    round(
                        success
                        / len(metas)
                        * 100,
                        2,
                    )
                    if metas
                    else 0
                ),

            "next_offset":
                (
                    next_offset
                    if has_more
                    else None
                ),

            "has_more":
                has_more,

            "results":
                results,
        }

    # ========================================================
    # VALIDATE ALL
    # ========================================================

    def validate_all_tables(
        self,
        limit: int = 0,
        prefer_local: bool = False,
    ) -> dict[str, Any]:

        """
        전체 supported table 검증 wrapper.
        """

        all_count = len(
            self.store
            .available_supported_tables()
        )

        actual_limit = (
            all_count
            if (
                not limit
                or limit <= 0
            )
            else limit
        )

        return (
            self.validate_tables_batch(
                offset=0,
                limit=actual_limit,
                prefer_local=(
                    prefer_local
                ),
                include_success_results=True,
            )
        )
