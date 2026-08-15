import traceback
import uuid
from app.database import init_db, get_db, pg_engine, User


def main():
    try:
        print("Initializing databases...")
        init_db()
        print(f"PG engine URL: {pg_engine.url}")

        db_gen = get_db()
        db = next(db_gen)
        try:
            unique = uuid.uuid4().hex[:12]
            user = User(
                username=f"test_dual_{unique}",
                email=f"test_{unique}@example.com",
                hashed_password="x" * 60,
                role="user",
            )
            print(f"Inserting user: {user.username}")
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"SQLite user id: {user.id}")

            pg_user = db.pg.query(User).filter(User.username == user.username).first()
            if pg_user:
                print(f"PostgreSQL user id: {pg_user.id}")
            else:
                print("PostgreSQL user: NOT FOUND")
        finally:
            db.close()
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
