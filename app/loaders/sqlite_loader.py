import sqlite3
from pathlib import Path

from app.domain import ExtractedChunk


# Explicit allowlists ensure credentials, image paths and internal local paths
# never leave the source database or get sent to the embedding provider.
PROPERTY_COLUMNS = (
    "member_code", "bukken_bangou", "bukken_name", "bukken_url",
    "bukken_shubetu", "bukken_price", "address", "bukken_koutu",
    "bukken_point", "osusume_com", "kodawari_jouken", "bukken_madori",
    "tatemono_menseki", "tochi_menseki", "tikunengetu", "kaidate_kai",
    "tatemono_kouzou", "tochi_kenri", "youto_chiiki", "genkyou",
    "hikiwatashi_jiki", "torihiki_taiyou", "jouhou_koukai_bi",
    "last_update", "setubi_service", "bikou",
)

COMPANY_COLUMNS = (
    "member_code", "company_name", "kana_name", "homepage", "at_home_url",
    "hato_mark_url", "area_code", "representative", "tel", "fax",
    "postal_code", "address", "catchphrase", "opening_times", "closed_day",
    "access", "Company_Message", "Company_Feature", "License_Number",
    "Organization", "HoshoKyokai", "MainProperties", "UriKodateURL",
    "UriManshonURL", "UriTochiURL", "UriTenpoURL", "UriJimusyoURL",
    "UriSonotaURL", "hato_UriKodateURL", "hato_UriManshonURL",
    "hato_UriTochiURL", "hato_UriTenpoURL", "hato_UriJimusyoURL",
    "hato_UriSonotaURL", "last_update",
)

TABLE_COLUMNS = {
    "BukkenData": PROPERTY_COLUMNS,
    "company_info": COMPANY_COLUMNS,
}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _format_row(table: str, columns: tuple[str, ...], row: tuple) -> str:
    heading = "物件情報" if table == "BukkenData" else "不動産会社情報"
    lines = [heading, f"データ元テーブル: {table}"]
    for column, value in zip(columns, row, strict=True):
        if value is None or str(value).strip() == "":
            continue
        lines.append(f"{column}: {value}")
    return "\n".join(lines)


def load_sqlite_table(path: Path, table: str) -> list[ExtractedChunk]:
    columns = TABLE_COLUMNS.get(table)
    if columns is None:
        raise ValueError(f"SQLite table is not allowlisted: {table}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        selected = ", ".join(_quote_identifier(column) for column in columns)
        query = f"SELECT {selected} FROM {_quote_identifier(table)}"
        output = []
        for row_number, row in enumerate(connection.execute(query), start=1):
            identity = str(row[1] or row[0] or row_number)
            output.append(ExtractedChunk(
                text=_format_row(table, columns, row),
                chunk_type="sqlite_row",
                language="sqlite",
                symbol_name=identity[:200],
                sheet_name=table,
                cell_range=f"row:{row_number}",
            ))
        return output
    finally:
        connection.close()
