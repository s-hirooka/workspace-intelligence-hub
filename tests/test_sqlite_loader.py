import sqlite3
from pathlib import Path

from app.loaders.sqlite_loader import COMPANY_COLUMNS, load_sqlite_table


def test_company_loader_excludes_password_and_local_paths(tmp_path: Path):
    path = tmp_path / "sample.sqlite"
    connection = sqlite3.connect(path)
    definitions = [f'"{column}" TEXT' for column in COMPANY_COLUMNS]
    definitions.extend(['"member_password" TEXT', '"local_path001" TEXT'])
    connection.execute(f'CREATE TABLE company_info ({", ".join(definitions)})')
    values = {column: "" for column in COMPANY_COLUMNS}
    values.update(
        member_code="C001",
        company_name="架空不動産",
        at_home_url="https://www.athome.co.jp/example",
    )
    insert_columns = (*COMPANY_COLUMNS, "member_password", "local_path001")
    quoted_columns = ", ".join(f'"{column}"' for column in insert_columns)
    placeholders = ", ".join("?" for _ in insert_columns)
    connection.execute(
        f"INSERT INTO company_info ({quoted_columns}) VALUES ({placeholders})",
        [*(values[column] for column in COMPANY_COLUMNS), "do-not-index", "C:/private/image.jpg"],
    )
    connection.commit()
    connection.close()

    chunks = load_sqlite_table(path, "company_info")
    assert len(chunks) == 1
    assert "架空不動産" in chunks[0].text
    assert "athome.co.jp" in chunks[0].text
    assert "do-not-index" not in chunks[0].text
    assert "C:/private/image.jpg" not in chunks[0].text
    assert "member_password" not in chunks[0].text
