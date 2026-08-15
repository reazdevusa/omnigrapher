import traceback
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import insert

from app.database import (
    init_db,
    sqlite_engine,
    pg_engine,
    sqlite_session_factory,
    pg_session_factory,
    User,
    Document,
    Feedback,
    Job,
    WidgetConfig,
)

# Sync order matters for foreign keys: users first, then dependent tables.
MODEL_ORDER = [User, Document, Feedback, Job, WidgetConfig]


def _type_name(col):
    return str(col["type"])


def verify_schemas():
    print("--- Schema parity check ---")
    sqlite_insp = inspect(sqlite_engine)
    pg_insp = inspect(pg_engine)
    mismatches = []

    for model in MODEL_ORDER:
        table = model.__tablename__
        try:
            sqlite_cols = {c["name"]: c for c in sqlite_insp.get_columns(table)}
            pg_cols = {c["name"]: c for c in pg_insp.get_columns(table)}
        except Exception as e:
            mismatches.append(f"{table}: could not inspect one side ({e})")
            continue

        all_cols = set(sqlite_cols.keys()) | set(pg_cols.keys())
        for col in sorted(all_cols):
            s = sqlite_cols.get(col)
            p = pg_cols.get(col)
            if not s:
                mismatches.append(f"{table}.{col}: missing in SQLite")
                continue
            if not p:
                mismatches.append(f"{table}.{col}: missing in PostgreSQL")
                continue
            if _type_name(s) != _type_name(p):
                mismatches.append(
                    f"{table}.{col}: type mismatch sqlite={_type_name(s)}, pg={_type_name(p)}"
                )
            if s.get("nullable") != p.get("nullable"):
                mismatches.append(
                    f"{table}.{col}: nullable mismatch sqlite={s.get('nullable')}, pg={p.get('nullable')}"
                )

    if mismatches:
        print("Schema differences found:")
        for m in mismatches:
            print(f"  - {m}")
    else:
        print("All table schemas match (columns, types, nullability).")
    return mismatches


def sync_table(sqlite_session, pg_session, model):
    """Bulk insert rows from SQLite into a freshly cleared PostgreSQL table."""
    table = model.__table__
    columns = list(table.columns.keys())
    records = sqlite_session.query(model).all()
    if not records:
        return 0

    rows = [{col: getattr(r, col) for col in columns} for r in records]
    pg_session.execute(table.insert().values(rows))
    return len(rows)


def update_sequence(pg_session, table_name):
    try:
        pg_session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                f"(SELECT GREATEST(COALESCE(MAX(id), 0), 1) FROM {table_name}), "
                f"(SELECT MAX(id) IS NOT NULL FROM {table_name}))"
            )
        )
        pg_session.commit()
    except Exception as e:
        print(f"  Could not update {table_name} id sequence: {e}")
        pg_session.rollback()


def log_counts(sqlite_session, pg_session):
    print("\n--- Final record counts ---")
    print(f"{'Table':<20} {'SQLite':>10} {'PostgreSQL':>10} {'Status':>10}")
    print("-" * 55)
    all_ok = True
    for model in MODEL_ORDER:
        table = model.__tablename__
        sqlite_count = sqlite_session.query(model).count()
        pg_count = pg_session.query(model).count()
        status = "OK" if sqlite_count == pg_count else "MISMATCH"
        if sqlite_count != pg_count:
            all_ok = False
        print(f"{table:<20} {sqlite_count:>10} {pg_count:>10} {status:>10}")
    return all_ok


def main():
    try:
        print("Initializing/creating tables in both databases...")
        init_db()

        mismatches = verify_schemas()
        if mismatches:
            print("Schema differences detected above; continuing with data sync.\n")
        else:
            print("Proceeding to data migration.\n")

        sqlite_session = sqlite_session_factory()
        pg_session = pg_session_factory()
        try:
            # Clear PG tables in reverse FK order to avoid constraint errors,
            # then reload from SQLite for a perfect mirror.
            print("--- Clearing PostgreSQL tables for clean mirror ---")
            for model in reversed(MODEL_ORDER):
                pg_session.execute(model.__table__.delete())
            pg_session.commit()

            print("--- Migrating data from SQLite to PostgreSQL ---")
            for model in MODEL_ORDER:
                table_name = model.__tablename__
                count = sync_table(sqlite_session, pg_session, model)
                print(f"Inserted {count} rows into {table_name}")
            pg_session.commit()

            for model in MODEL_ORDER:
                update_sequence(pg_session, model.__tablename__)

            all_ok = log_counts(sqlite_session, pg_session)
            if all_ok:
                print("\nSynchronization complete: both databases are mirrored.")
            else:
                print("\nSynchronization finished with count mismatches.")
        finally:
            sqlite_session.close()
            pg_session.close()
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
