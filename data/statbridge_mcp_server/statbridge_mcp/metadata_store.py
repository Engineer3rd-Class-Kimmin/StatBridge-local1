from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

import pandas as pd

from .config import settings
from .utils import unique_join


FREQ_MAP = {
    "일": "D",
    "월": "M",
    "분기": "Q",
    "반기": "S",
    "년": "Y",
    "연": "Y",
    "연간": "Y",
    "부정기": "IR",
    "D": "D",
    "M": "M",
    "Q": "Q",
    "S": "S",
    "Y": "Y",
    "IR": "IR",
}

KNOWN_OPENAPI_EXCEPTIONS = {"DT_284Y001", "DT_284Y002"}


def normalize_frequency(value: str) -> str:
    value = str(value or "").strip()
    return FREQ_MAP.get(value, value)


def normalize_period(freq: str, value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""

    freq = normalize_frequency(freq)

    if freq == "Y":
        m = re.search(r"(\d{4})", s)
        return m.group(1) if m else s

    if freq == "M":
        m = re.search(r"(\d{4})\D*([01]?\d)", s)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"

    if freq == "Q":
        m = re.search(r"(\d{4}).*?([1-4])(?:/4|분기|Q)?", s, re.I)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"

    if freq == "S":
        m = re.search(r"(\d{4}).*?([1-2])(?:/2|반기)?", s)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"

    if freq == "D":
        digits = re.sub(r"\D", "", s)
        return digits if len(digits) == 8 else s

    return s


@dataclass(slots=True)
class TableMetadata:
    # Canonical v1.0
    schema_version: str
    table_id: str
    org_id: str
    stat_id: str
    table_name: str
    table_name_eng: str | None
    path: str
    send_date: str | None
    api_status: str
    frequencies: list[str]
    periods: list[dict[str, Any]]
    items: list[dict[str, Any]]
    dimensions: list[dict[str, Any]]
    comments_structured: list[dict[str, Any]]
    source: dict[str, Any]
    search_text: str

    # 기존 SearchEngine / StatisticsService 호환용
    path_text: str
    start_period: str | None
    end_period: str | None
    classifications: list[dict[str, Any]]
    comments: list[str]
    sources: list[str]

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "table_id": self.table_id,
            "org_id": self.org_id,
            "stat_id": self.stat_id,
            "table_name": self.table_name,
            "table_name_eng": self.table_name_eng,
            "path": self.path,
            "send_date": self.send_date,
            "api_status": self.api_status,
            "frequencies": self.frequencies,
            "periods": self.periods,
            "items": self.items,
            "dimensions": self.dimensions,
            "comments": self.comments_structured,
            "source": self.source,
            "search_text": self.search_text,
            "period_source": "local_metadata",
        }


class MetadataStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = (data_dir or settings.data_dir).resolve()

        self.table_master = self._read_csv("bok_table_master.csv")
        self.items = self._read_csv("bok_items.csv")
        self.classifications = self._read_csv(
            "bok_classifications.csv"
        )
        self.periods = self._read_csv(
            "bok_periods.csv",
            required=False,
        )
        self.comments = self._read_csv(
            "bok_comments.csv",
            required=False,
        )
        self.sources = self._read_csv(
            "bok_sources.csv",
            required=False,
        )

        self._index = self._build_index()

    def _read_csv(
        self,
        name: str,
        required: bool = True,
    ) -> pd.DataFrame:
        path = self.data_dir / name

        if not path.exists():
            if required:
                raise FileNotFoundError(
                    f"필수 파일이 없습니다: {path}"
                )

            return pd.DataFrame()

        return pd.read_csv(
            path,
            dtype=str,
        ).fillna("")

    def _col(
        self,
        df: pd.DataFrame,
        *names: str,
    ) -> str | None:
        if df.empty:
            return None

        mapping = {
            c.replace("_", "").lower(): c
            for c in df.columns
        }

        for name in names:
            c = mapping.get(
                name.replace("_", "").lower()
            )

            if c:
                return c

        return None

    @staticmethod
    def _split_multi(
        value: str,
    ) -> list[str]:
        s = str(value or "").strip()

        if not s:
            return []

        parts = re.split(r"[|,;/]+", s)

        out: list[str] = []

        for part in parts:
            part = part.strip()

            if part and part not in out:
                out.append(part)

        return out

    @staticmethod
    def _uniq(
        values: list[str],
    ) -> list[str]:
        out: list[str] = []

        for value in values:
            value = str(value or "").strip()

            if value and value not in out:
                out.append(value)

        return out

    def _build_index(
        self,
    ) -> dict[str, TableMetadata]:
        idx: dict[str, TableMetadata] = {}

        if self.table_master.empty:
            return idx

        # -------------------------------------------------
        # table master
        # -------------------------------------------------

        tbl_id_col = self._col(
            self.table_master,
            "TBL_ID",
            "tblId",
        )

        tbl_nm_col = self._col(
            self.table_master,
            "TBL_NM",
            "tblNm",
            "STAT_NAME",
        )

        tbl_nm_eng_col = self._col(
            self.table_master,
            "TBL_NM_ENG",
            "tblNmEng",
        )

        org_col = self._col(
            self.table_master,
            "ORG_ID",
            "orgId",
        )

        stat_id_col = self._col(
            self.table_master,
            "STAT_ID",
            "statId",
        )

        path_col = self._col(
            self.table_master,
            "PATH",
        )

        send_col = self._col(
            self.table_master,
            "SEND_DE",
            "sendDe",
        )

        master_freq_col = self._col(
            self.table_master,
            "FREQUENCIES",
            "FREQUENCY",
            "PRD_SE",
        )

        master_start_col = self._col(
            self.table_master,
            "START_PERIOD",
            "START_PRD_DE",
            "STRT_PRD_DE",
        )

        master_end_col = self._col(
            self.table_master,
            "END_PERIOD",
            "END_PRD_DE",
        )

        master_comments_col = self._col(
            self.table_master,
            "COMMENTS",
            "COMMENT",
        )

        master_source_col = self._col(
            self.table_master,
            "JOSA_NM",
            "SOURCE",
            "SOURCES",
        )

        master_dept_col = self._col(
            self.table_master,
            "DEPT_NM",
        )

        master_phone_col = self._col(
            self.table_master,
            "DEPT_PHONE",
        )

        # -------------------------------------------------
        # items
        # -------------------------------------------------

        item_tbl_col = self._col(
            self.items,
            "TBL_ID",
            "TBL_ID_BASE",
            "tblId",
        )

        item_id_col = self._col(
            self.items,
            "ITM_ID",
            "itmId",
        )

        item_nm_col = self._col(
            self.items,
            "ITM_NM",
            "itmNm",
        )

        item_nm_eng_col = self._col(
            self.items,
            "ITM_NM_ENG",
            "itmNmEng",
        )

        # -------------------------------------------------
        # classifications
        # -------------------------------------------------

        cls_tbl_col = self._col(
            self.classifications,
            "TBL_ID",
            "TBL_ID_BASE",
            "tblId",
        )

        cls_obj_col = self._col(
            self.classifications,
            "OBJ_ID",
            "objId",
        )

        cls_obj_sn_col = self._col(
            self.classifications,
            "OBJ_ID_SN",
            "objIdSn",
        )

        cls_obj_nm_col = self._col(
            self.classifications,
            "OBJ_NM",
            "objNm",
        )

        cls_id_col = self._col(
            self.classifications,
            "CLS_ID",
            "ITM_ID",
            "itmId",
        )

        cls_nm_col = self._col(
            self.classifications,
            "CLS_NM",
            "ITM_NM",
            "itmNm",
        )

        cls_parent_col = self._col(
            self.classifications,
            "UP_ITM_ID",
            "upItmId",
        )

        cls_unit_id_col = self._col(
            self.classifications,
            "UNIT_ID",
            "unitId",
        )

        cls_unit_nm_col = self._col(
            self.classifications,
            "UNIT_NM",
            "unitNm",
        )

        cls_unit_eng_col = self._col(
            self.classifications,
            "UNIT_ENG_NM",
            "unitEngNm",
        )

        # -------------------------------------------------
        # periods
        # 실제 collector 컬럼:
        # TBL_ID_BASE / PRD_SE /
        # STRT_PRD_DE / END_PRD_DE
        # -------------------------------------------------

        prd_tbl_col = self._col(
            self.periods,
            "TBL_ID_BASE",
            "TBL_ID",
            "tblId",
        )

        prd_se_col = self._col(
            self.periods,
            "PRD_SE",
            "prdSe",
        )

        prd_start_col = self._col(
            self.periods,
            "STRT_PRD_DE",
            "START_PRD_DE",
            "START_PERIOD",
        )

        prd_end_col = self._col(
            self.periods,
            "END_PRD_DE",
            "END_PERIOD",
        )

        prd_de_col = self._col(
            self.periods,
            "PRD_DE",
            "prdDe",
        )

        # -------------------------------------------------
        # comments
        # CMMT_NM = 주석 유형
        # CMMT_DC = 실제 주석 본문
        # -------------------------------------------------

        cmt_tbl_col = self._col(
            self.comments,
            "TBL_ID_BASE",
            "TBL_ID",
            "tblId",
        )

        cmt_type_col = self._col(
            self.comments,
            "CMMT_NM",
            "CMT_NM",
        )

        cmt_text_col = self._col(
            self.comments,
            "CMMT_DC",
            "CMT_DC",
            "COMMENT",
            "NOTE",
        )

        cmt_item_col = self._col(
            self.comments,
            "ITM_ID",
            "itmId",
        )

        cmt_obj_col = self._col(
            self.comments,
            "OBJ_ID",
            "objId",
        )

        # -------------------------------------------------
        # sources
        # -------------------------------------------------

        src_tbl_col = self._col(
            self.sources,
            "TBL_ID_BASE",
            "TBL_ID",
            "tblId",
        )

        src_josa_col = self._col(
            self.sources,
            "JOSA_NM",
            "SRC_NM",
            "SOURCE",
        )

        src_dept_col = self._col(
            self.sources,
            "DEPT_NM",
        )

        src_phone_col = self._col(
            self.sources,
            "DEPT_PHONE",
        )

        # -------------------------------------------------
        # build
        # -------------------------------------------------

        for _, row in self.table_master.iterrows():

            table_id = (
                str(row[tbl_id_col]).strip()
                if tbl_id_col
                else ""
            )

            if not table_id:
                continue

            table_name = (
                str(row[tbl_nm_col]).strip()
                if tbl_nm_col
                else table_id
            )

            table_name_eng = (
                str(row[tbl_nm_eng_col]).strip()
                if tbl_nm_eng_col
                else ""
            )

            org_id = (
                str(row[org_col]).strip()
                if org_col
                else "301"
            )

            stat_id = (
                str(row[stat_id_col]).strip()
                if stat_id_col
                else ""
            )

            send_date = (
                str(row[send_col]).strip()
                if send_col
                else ""
            )

            # ---------------------------------------------
            # path
            # ---------------------------------------------

            if path_col:
                path_text = str(
                    row[path_col]
                ).strip()

            else:
                path_cols = [
                    c
                    for c in self.table_master.columns
                    if (
                        "list" in c.lower()
                        or "path" in c.lower()
                        or "lvl" in c.lower()
                    )
                ]

                path_text = unique_join(
                    [
                        str(row[c])
                        for c in path_cols
                        if str(row[c]).strip()
                    ]
                )

            # ---------------------------------------------
            # items
            # ---------------------------------------------

            item_rows: list[dict[str, Any]] = []

            if (
                item_tbl_col
                and item_id_col
                and item_nm_col
            ):

                sub = self.items[
                    self.items[item_tbl_col]
                    == table_id
                ]

                for _, r in sub.iterrows():

                    item_id = str(
                        r[item_id_col]
                    ).strip()

                    if not item_id:
                        continue

                    item_rows.append(
                        {
                            "item_id": item_id,
                            "item_name": str(
                                r[item_nm_col]
                            ).strip(),
                            "item_name_eng": (
                                str(
                                    r[
                                        item_nm_eng_col
                                    ]
                                ).strip()
                                if item_nm_eng_col
                                else None
                            ),
                        }
                    )

            # ---------------------------------------------
            # classifications
            # OBJ_ID_SN 기준으로 dimension 순서 확정
            # ---------------------------------------------

            flat_classes: list[
                dict[str, Any]
            ] = []

            dimensions_map: dict[
                int,
                dict[str, Any],
            ] = {}

            if (
                cls_tbl_col
                and cls_obj_col
                and cls_id_col
            ):

                sub = self.classifications[
                    self.classifications[
                        cls_tbl_col
                    ]
                    == table_id
                ]

                for _, r in sub.iterrows():

                    cid = str(
                        r[cls_id_col]
                    ).strip()

                    oid = str(
                        r[cls_obj_col]
                    ).strip()

                    if not cid or not oid:
                        continue

                    try:
                        dim_index = (
                            int(
                                str(
                                    r[
                                        cls_obj_sn_col
                                    ]
                                ).strip()
                            )
                            if (
                                cls_obj_sn_col
                                and str(
                                    r[
                                        cls_obj_sn_col
                                    ]
                                ).strip()
                            )
                            else 1
                        )

                    except ValueError:
                        dim_index = 1

                    obj_name = (
                        str(
                            r[
                                cls_obj_nm_col
                            ]
                        ).strip()
                        if cls_obj_nm_col
                        else ""
                    )

                    class_row = {
                        "dimension_index":
                            dim_index,
                        "mcp_param":
                            f"objL{dim_index}",
                        "obj_id":
                            oid,
                        "obj_name":
                            obj_name,
                        "class_id":
                            cid,
                        "class_name":
                            (
                                str(
                                    r[
                                        cls_nm_col
                                    ]
                                ).strip()
                                if cls_nm_col
                                else ""
                            ),
                        "parent_class_id":
                            (
                                str(
                                    r[
                                        cls_parent_col
                                    ]
                                ).strip()
                                or None
                                if cls_parent_col
                                else None
                            ),
                        "unit_id":
                            (
                                str(
                                    r[
                                        cls_unit_id_col
                                    ]
                                ).strip()
                                or None
                                if cls_unit_id_col
                                else None
                            ),
                        "unit_name":
                            (
                                str(
                                    r[
                                        cls_unit_nm_col
                                    ]
                                ).strip()
                                or None
                                if cls_unit_nm_col
                                else None
                            ),
                        "unit_name_eng":
                            (
                                str(
                                    r[
                                        cls_unit_eng_col
                                    ]
                                ).strip()
                                or None
                                if cls_unit_eng_col
                                else None
                            ),
                    }

                    flat_classes.append(
                        class_row
                    )

                    dim = dimensions_map.setdefault(
                        dim_index,
                        {
                            "dimension_index":
                                dim_index,
                            "mcp_param":
                                f"objL{dim_index}",
                            "obj_id":
                                oid,
                            "obj_name":
                                obj_name,
                            "values":
                                [],
                        },
                    )

                    dim["values"].append(
                        {
                            "class_id":
                                class_row[
                                    "class_id"
                                ],
                            "class_name":
                                class_row[
                                    "class_name"
                                ],
                            "parent_class_id":
                                class_row[
                                    "parent_class_id"
                                ],
                            "unit_id":
                                class_row[
                                    "unit_id"
                                ],
                            "unit_name":
                                class_row[
                                    "unit_name"
                                ],
                            "unit_name_eng":
                                class_row[
                                    "unit_name_eng"
                                ],
                        }
                    )

            dimensions = [
                dimensions_map[k]
                for k in sorted(
                    dimensions_map
                )
            ]

            flat_classes.sort(
                key=lambda x: (
                    x.get(
                        "dimension_index",
                        999,
                    ),
                    x.get(
                        "obj_id",
                        "",
                    ),
                    x.get(
                        "class_id",
                        "",
                    ),
                )
            )

            # ---------------------------------------------
            # periods
            # 주기별 기간을 독립적으로 유지
            # ---------------------------------------------

            frequencies: list[str] = []

            period_rows: list[
                dict[str, Any]
            ] = []

            if (
                prd_tbl_col
                and not self.periods.empty
            ):

                sub = self.periods[
                    self.periods[
                        prd_tbl_col
                    ]
                    == table_id
                ]

                current_freq = ""

                for _, pr in sub.iterrows():

                    raw_freq = (
                        str(
                            pr[
                                prd_se_col
                            ]
                        ).strip()
                        if prd_se_col
                        else ""
                    )

                    if raw_freq:
                        current_freq = (
                            normalize_frequency(
                                raw_freq
                            )
                        )

                    if not current_freq:
                        continue

                    if (
                        current_freq
                        not in frequencies
                    ):
                        frequencies.append(
                            current_freq
                        )

                    raw_start = (
                        str(
                            pr[
                                prd_start_col
                            ]
                        ).strip()
                        if prd_start_col
                        else ""
                    )

                    raw_end = (
                        str(
                            pr[
                                prd_end_col
                            ]
                        ).strip()
                        if prd_end_col
                        else ""
                    )

                    raw_single = (
                        str(
                            pr[
                                prd_de_col
                            ]
                        ).strip()
                        if prd_de_col
                        else ""
                    )

                    if (
                        raw_single
                        and not raw_start
                        and not raw_end
                    ):
                        raw_start = raw_single
                        raw_end = raw_single

                    if raw_start or raw_end:

                        start_norm = (
                            normalize_period(
                                current_freq,
                                raw_start,
                            )
                            if raw_start
                            else ""
                        )

                        end_norm = (
                            normalize_period(
                                current_freq,
                                raw_end,
                            )
                            if raw_end
                            else ""
                        )

                        period_rows.append(
                            {
                                "frequency":
                                    current_freq,
                                "start_period":
                                    start_norm,
                                "end_period":
                                    end_norm,
                                "start_period_raw":
                                    (
                                        raw_start
                                        or None
                                    ),
                                "end_period_raw":
                                    (
                                        raw_end
                                        or None
                                    ),
                            }
                        )

            # master fallback
            if (
                not frequencies
                and master_freq_col
            ):
                for raw in self._split_multi(
                    str(
                        row[
                            master_freq_col
                        ]
                    )
                ):

                    f = normalize_frequency(
                        raw
                    )

                    if (
                        f
                        and f
                        not in frequencies
                    ):
                        frequencies.append(f)

            # 상세 기간이 없으면 master fallback
            if (
                not period_rows
                and frequencies
            ):

                raw_start = (
                    str(
                        row[
                            master_start_col
                        ]
                    ).strip()
                    if master_start_col
                    else ""
                )

                raw_end = (
                    str(
                        row[
                            master_end_col
                        ]
                    ).strip()
                    if master_end_col
                    else ""
                )

                if raw_start or raw_end:

                    f = frequencies[0]

                    period_rows.append(
                        {
                            "frequency":
                                f,
                            "start_period":
                                (
                                    normalize_period(
                                        f,
                                        raw_start,
                                    )
                                    if raw_start
                                    else ""
                                ),
                            "end_period":
                                (
                                    normalize_period(
                                        f,
                                        raw_end,
                                    )
                                    if raw_end
                                    else ""
                                ),
                            "start_period_raw":
                                (
                                    raw_start
                                    or None
                                ),
                            "end_period_raw":
                                (
                                    raw_end
                                    or None
                                ),
                        }
                    )

            normalized_starts = [
                p["start_period"]
                for p in period_rows
                if p.get(
                    "start_period"
                )
            ]

            normalized_ends = [
                p["end_period"]
                for p in period_rows
                if p.get(
                    "end_period"
                )
            ]

            start_period = (
                min(normalized_starts)
                if normalized_starts
                else None
            )

            end_period = (
                max(normalized_ends)
                if normalized_ends
                else None
            )

            # ---------------------------------------------
            # comments
            # ---------------------------------------------

            comments_structured: list[
                dict[str, Any]
            ] = []

            if (
                cmt_tbl_col
                and not self.comments.empty
            ):

                sub = self.comments[
                    self.comments[
                        cmt_tbl_col
                    ]
                    == table_id
                ]

                for _, r in sub.iterrows():

                    text = (
                        str(
                            r[
                                cmt_text_col
                            ]
                        ).strip()
                        if cmt_text_col
                        else ""
                    )

                    if not text:
                        continue

                    comments_structured.append(
                        {
                            "comment_type":
                                (
                                    str(
                                        r[
                                            cmt_type_col
                                        ]
                                    ).strip()
                                    or None
                                    if cmt_type_col
                                    else None
                                ),
                            "comment_text":
                                text,
                            "item_id":
                                (
                                    str(
                                        r[
                                            cmt_item_col
                                        ]
                                    ).strip()
                                    or None
                                    if cmt_item_col
                                    else None
                                ),
                            "obj_id":
                                (
                                    str(
                                        r[
                                            cmt_obj_col
                                        ]
                                    ).strip()
                                    or None
                                    if cmt_obj_col
                                    else None
                                ),
                        }
                    )

            if (
                not comments_structured
                and master_comments_col
            ):

                raw = str(
                    row[
                        master_comments_col
                    ]
                ).strip()

                if raw:
                    comments_structured = [
                        {
                            "comment_type":
                                "table",
                            "comment_text":
                                raw,
                            "item_id":
                                None,
                            "obj_id":
                                None,
                        }
                    ]

            comments = self._uniq(
                [
                    c["comment_text"]
                    for c
                    in comments_structured
                ]
            )

            # ---------------------------------------------
            # source
            # ---------------------------------------------

            source = {
                "survey_name": "",
                "department_name": "",
                "department_phone": "",
            }

            if (
                src_tbl_col
                and not self.sources.empty
            ):

                sub = self.sources[
                    self.sources[
                        src_tbl_col
                    ]
                    == table_id
                ]

                if not sub.empty:

                    r = sub.iloc[0]

                    source = {
                        "survey_name":
                            (
                                str(
                                    r[
                                        src_josa_col
                                    ]
                                ).strip()
                                if src_josa_col
                                else ""
                            ),
                        "department_name":
                            (
                                str(
                                    r[
                                        src_dept_col
                                    ]
                                ).strip()
                                if src_dept_col
                                else ""
                            ),
                        "department_phone":
                            (
                                str(
                                    r[
                                        src_phone_col
                                    ]
                                ).strip()
                                if src_phone_col
                                else ""
                            ),
                    }

            if (
                not source["survey_name"]
                and master_source_col
            ):
                source["survey_name"] = (
                    str(
                        row[
                            master_source_col
                        ]
                    ).strip()
                )

            if (
                not source[
                    "department_name"
                ]
                and master_dept_col
            ):
                source[
                    "department_name"
                ] = str(
                    row[
                        master_dept_col
                    ]
                ).strip()

            if (
                not source[
                    "department_phone"
                ]
                and master_phone_col
            ):
                source[
                    "department_phone"
                ] = str(
                    row[
                        master_phone_col
                    ]
                ).strip()

            sources = self._uniq(
                [
                    source[
                        "survey_name"
                    ],
                    source[
                        "department_name"
                    ],
                    source[
                        "department_phone"
                    ],
                ]
            )

            # ---------------------------------------------
            # API status
            # ---------------------------------------------

            api_status = (
                "KOSIS_OPENAPI_EXCEPTION"
                if table_id
                in KNOWN_OPENAPI_EXCEPTIONS
                else "SUPPORTED"
            )

            # ---------------------------------------------
            # search text
            # ---------------------------------------------

            search_text = unique_join(
                [
                    table_name,
                    table_name_eng,
                    path_text,
                    " ".join(
                        i["item_name"]
                        for i
                        in item_rows
                    ),
                    " ".join(
                        c["class_name"]
                        for c
                        in flat_classes
                    ),
                    " ".join(
                        d["obj_name"]
                        for d
                        in dimensions
                    ),
                    " ".join(
                        frequencies
                    ),
                    " ".join(
                        comments[:10]
                    ),
                    " ".join(
                        sources[:5]
                    ),
                ],
                sep=" ",
            )

            # ---------------------------------------------
            # final object
            # ---------------------------------------------

            idx[table_id] = TableMetadata(
                schema_version="1.0.0",
                table_id=table_id,
                org_id=org_id,
                stat_id=stat_id,
                table_name=table_name,
                table_name_eng=(
                    table_name_eng
                    or None
                ),
                path=path_text,
                send_date=(
                    send_date
                    or None
                ),
                api_status=api_status,
                frequencies=frequencies,
                periods=period_rows,
                items=item_rows,
                dimensions=dimensions,
                comments_structured=(
                    comments_structured
                ),
                source=source,
                search_text=search_text,

                # compatibility
                path_text=path_text,
                start_period=start_period,
                end_period=end_period,
                classifications=flat_classes,
                comments=comments,
                sources=sources,
            )

        return idx

    def table_ids(
        self,
    ) -> list[str]:
        return list(
            self._index.keys()
        )

    def get(
        self,
        table_id: str,
    ) -> TableMetadata | None:
        return self._index.get(
            table_id
        )

    def all(
        self,
    ) -> list[TableMetadata]:
        return list(
            self._index.values()
        )

    def is_supported(
        self,
        table_id: str,
    ) -> bool:
        meta = self._index.get(
            table_id
        )

        return bool(
            meta
            and meta.api_status
            == "SUPPORTED"
        )

    def available_supported_tables(
        self,
    ) -> list[TableMetadata]:
        return [
            m
            for m
            in self._index.values()
            if (
                m.api_status
                == "SUPPORTED"
            )
        ]