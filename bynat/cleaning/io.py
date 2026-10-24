"""JSON and SQL export helpers, and the JSON/CSV loaders."""

import json
import os
import urllib.parse
from typing import Any, Literal

import pandas as pd

from ._common import _dedupe_key, _import_optional, logger

JsonOrient = Literal['split', 'records', 'index', 'columns', 'values', 'table']
IfExists = Literal['fail', 'replace', 'append']


def read_json(
    json_file_path: str | os.PathLike,
    key: str | None = None,
) -> pd.DataFrame:
    """
    Read a JSON file into a plain `pandas.DataFrame`.

    The payload is flattened with `pandas.json_normalize`, as `JsonFile`
    does. Use the `df.bynat` accessor on the result for bynat methods.

    Args:
        json_file_path (str | os.PathLike): Path to the JSON file.
        key (str | None): Optional top-level key whose value holds the
            records, for dictionary payloads. Defaults to None.

    Returns:
        pandas.DataFrame: The normalized records.

    Raises:
        FileNotFoundError: If the file does not exist.
        TypeError: If `key` is given for a non-dictionary payload.
        KeyError: If `key` is not in the payload.

    Example:
    ```
        import bynat

        jobs = bynat.read_json("jobs.json", key="items")
        jobs = jobs.bynat.rename_all_DOTS()
    ```
    """
    path = os.fspath(json_file_path)

    if not os.path.isfile(path):
        raise FileNotFoundError(f"JSON file not found: {path}")

    with open(path, encoding = "utf-8") as file:
        raw_data = json.load(file)

    if key:
        if not isinstance(raw_data, dict):
            raise TypeError("A JSON key can only be selected from a dictionary payload.")

        if key not in raw_data:
            raise KeyError(f"Key '{key}' was not found in JSON payload.")

        raw_data = raw_data[key]

    return pd.json_normalize(raw_data)


def read_csv(csv_file_path: str | os.PathLike, **read_kwargs: Any) -> pd.DataFrame:
    """
    Read a CSV file into a plain `pandas.DataFrame`.

    Args:
        csv_file_path (str | os.PathLike): Path to the CSV file.
        **read_kwargs: Keyword arguments forwarded to `pandas.read_csv`,
            replacing `CsvFile(read_kwargs=...)`.

    Returns:
        pandas.DataFrame: The parsed file.

    Raises:
        FileNotFoundError: If the file does not exist.

    Example:
    ```
        import bynat

        people = bynat.read_csv("people.csv", sep=";")
    ```
    """
    path = os.fspath(csv_file_path)

    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV file not found: {path}")

    return pd.read_csv(path, **read_kwargs)


def save_to_json(
    df: pd.DataFrame,
    file_path: str | os.PathLike,
    orient: JsonOrient = 'records',
    append: bool = False,
    force_ascii: bool = False,
    indent: int = 4
) -> None:
    """
    Save the dataframe to a JSON file only when content has changed.

    Notes:
        Before writing, the method compares the current dataframe payload to
        the existing file contents when possible. This avoids unnecessary
        rewrites and keeps the output directory stable across unchanged runs.
        Datetime columns are serialized as `%Y-%m-%d %H:%M:%S` strings to
        keep the JSON output human-readable and stable across environments.
        Paths without a directory component, such as `output.json`, are
        written to the current working directory.

    Args:
        file_path (str | os.PathLike): Destination JSON file path.
        orient (str): JSON orientation passed to `pandas.DataFrame.to_json`.
            Defaults to `"records"`.
        append (bool): If True and the target file exists, appends current
            rows to the existing JSON data and removes duplicate rows before
            writing. Defaults to False.
        force_ascii (bool): Whether to escape non-ASCII characters in the
            output. Defaults to False.
        indent (int): Indentation width used when writing to disk. Defaults
            to 4.

    Returns:
        None
    """
    file_path = os.fspath(file_path).strip() if file_path is not None else ""

    if not file_path:
        raise ValueError("file_path cannot be empty.")

    export_df = df.copy()

    for col in export_df.select_dtypes(include = ['datetime', 'datetimetz']).columns:
        export_df[col] = export_df[col].dt.strftime('%Y-%m-%d %H:%M:%S')

    is_append_successful = False
    new_rows_count = len(export_df)

    duplicates_removed = 0

    if os.path.exists(file_path):
        if append:
            try:
                existing_df = pd.read_json(file_path, orient = orient)

            except ValueError as e:
                # Overwriting here would silently destroy the existing file.
                raise ValueError(
                    f"Cannot append to '{file_path}': it is not readable as orient='{orient}' JSON."
                ) from e

            combined_df = pd.concat([existing_df, export_df], ignore_index = True)

            initial_count = len(combined_df)

            try:
                duplicate_mask = combined_df.duplicated()

            except TypeError:
                # Lists and dicts are unhashable, so compare object columns by a JSON key.
                dedupe_view = combined_df.copy(deep = False)

                for col in dedupe_view.select_dtypes(include = 'object').columns:
                    dedupe_view[col] = dedupe_view[col].map(_dedupe_key)

                duplicate_mask = dedupe_view.duplicated()

            combined_df = combined_df[~duplicate_mask].reset_index(drop = True)
            duplicates_removed = initial_count - len(combined_df)

            export_df = combined_df
            is_append_successful = True

        else:
            try:
                json_str = export_df.to_json(orient = orient, force_ascii = force_ascii)
                current_data = json.loads(json_str)

                with open(file_path, encoding = 'utf-8') as f:
                    existing_data = json.load(f)

                if current_data == existing_data:
                    logger.info("[SKIP] No changes detected. '%s' is already up to date.", os.path.basename(file_path))

                    return None

            except Exception as e:
                logger.warning("Could not read existing file for comparison (%s). Overwriting...", e)

    output_dir = os.path.dirname(file_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok = True)

    export_df.to_json(file_path, orient = orient, force_ascii = force_ascii, indent = indent)

    if is_append_successful:
        logger.info("[APPEND] Added %s new rows. Removed %s duplicates. Total jobs: %s.", new_rows_count, duplicates_removed, len(export_df))

    else:
        logger.info("[OVERWRITE] Save completed successfully! Total rows: %s.", len(export_df))

    return None


def save_to_sql(
    df: pd.DataFrame,
    table_name: str,
    database_type: str,
    database_user: str,
    database_password: str,
    database_host: str,
    database_port: str,
    database_name: str,
    database_driver: str | None = None,
    columns_types: dict | None = None,
    chunk_size: int | None = None,
    fast_executemany: bool = False,
    if_exists: IfExists = 'append',
    index: bool = False
) -> None:
    """
    Save the dataframe to a SQL database table using SQLAlchemy.

    Notes:
        The method supports standard SQLAlchemy URL styles and includes
        dedicated handling for SQL Server drivers and SQLite paths. For
        SQL Server, `database_driver` is required and is URL-encoded.
        For SQLite, only `database_name` is required.

    Args:
        table_name (str): The name of the SQL table to write to.
        database_type (str): SQLAlchemy dialect/driver, for example
            `"postgresql+psycopg2"`, `"mysql+pymysql"`, `"mssql+pyodbc"`,
            or `"sqlite"`.
        database_user (str): Database username.
        database_password (str): Database password.
        database_host (str): Database host address.
        database_port (str): Database port.
        database_name (str): Database/schema name, or SQLite file path.
        database_driver (str | None): ODBC driver name for SQL Server
            dialects. Defaults to None.
        columns_types (dict | None): Optional SQLAlchemy dtype mapping to
            control target SQL column types. Defaults to None.
        chunk_size (int | None): Optional number of rows per write chunk.
            Defaults to None (single batch).
        fast_executemany (bool): Enables `fast_executemany` for SQL Server
            pyodbc connections when supported. Defaults to False.
        if_exists (str): How to behave if the table already exists.
            Options: 'fail', 'replace', 'append'. Defaults to 'append'.
        index (bool): Whether to write the DataFrame index as a SQL column.
            Defaults to False.

    Returns:
        None

    Example:
    ```
        df.save_to_sql(
            table_name="cleaned_search_data",
            database_type="postgresql+psycopg2",
            database_user="postgres",
            database_password="secret",
            database_host="localhost",
            database_port="5432",
            database_name="analytics_db",
            if_exists="replace",
            index=False
        )
    ```
    """

    db_type = str(database_type or '').strip().lower()
    db_user = str(database_user or '').strip()
    db_pass = str(database_password or '').strip()
    db_host = str(database_host or '').strip()
    db_port = str(database_port or '').strip()
    db_name = str(database_name or '').strip()
    db_driver = str(database_driver or '').strip() or None
    table_name = str(table_name or '').strip()
    engine_kwargs = {}

    if not table_name:
        raise ValueError("table_name cannot be empty.")

    if if_exists not in ['fail', 'replace', 'append']:
        raise ValueError(f"Invalid if_exists value '{if_exists}'. Use 'fail', 'replace', or 'append'.")

    if not db_type:
        raise ValueError("database_type cannot be empty.")

    if db_type.startswith('sqlite'):
        if not db_name:
            raise ValueError("database_name is required for SQLite.")

        if db_name == ':memory:':
            engine_url = "sqlite:///:memory:"

        else:
            engine_url = f"sqlite:///{db_name}"

    elif 'mssql' in db_type or 'sqlserver' in db_type:
        if not all([db_user, db_pass, db_host, db_port, db_name]):
            raise ValueError("SQL Server needs database_user, database_password, database_host, database_port, and database_name.")

        if not db_driver:
            raise ValueError("database_driver is required for SQL Server.")

        encoded_driver = urllib.parse.quote_plus(db_driver)
        encoded_user = urllib.parse.quote_plus(db_user)
        encoded_pass = urllib.parse.quote_plus(db_pass)

        engine_url = f"{db_type}://{encoded_user}:{encoded_pass}@{db_host}:{db_port}/{db_name}?driver={encoded_driver}"
        engine_kwargs['fast_executemany'] = fast_executemany

    else:

        if not all([db_user, db_pass, db_host, db_port, db_name]):
            raise ValueError("database_user, database_password, database_host, database_port, and database_name are required.")

        encoded_user = urllib.parse.quote_plus(db_user)
        encoded_pass = urllib.parse.quote_plus(db_pass)

        engine_url = f"{db_type}://{encoded_user}:{encoded_pass}@{db_host}:{db_port}/{db_name}"

    sqlalchemy = _import_optional("sqlalchemy", "SQLAlchemy", "save_to_sql")

    export_df = df.copy()

    # Normalize timezone-aware datetimes for SQL drivers that do not accept timezone-aware pandas dtypes.
    for col in export_df.select_dtypes(include = ['datetimetz']).columns:
        export_df[col] = export_df[col].dt.tz_localize(None)

    engine = sqlalchemy.create_engine(engine_url, **engine_kwargs)

    try:
        logger.info("Pushing data to SQL table '%s' at %s...", table_name, db_host)

        export_df.to_sql(
            table_name,
            con = engine,
            if_exists = if_exists,
            index = index,
            dtype = columns_types,
            chunksize = chunk_size if chunk_size else None
        )

        logger.info("Database save completed successfully!")

    finally:
        engine.dispose()

    return None
