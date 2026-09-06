import argparse
import getpass
import json
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import delete

from .catalog import import_catalog
from .db import BrowserSession, Database, User
from .security import password_hash


def main():
    parser = argparse.ArgumentParser(description="Quicker server administration")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Create or reset the shared account")
    setup.add_argument("--username", default="admin")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("migrate")
    qif = commands.add_parser("import-qif")
    qif.add_argument("path")
    backup = commands.add_parser("backup")
    backup.add_argument("destination")
    args = parser.parse_args()
    db = Database()
    db.migrate()
    if args.command == "setup":
        password = getpass.getpass("Shared account password (at least 12 characters): ")
        if len(password) < 12 or password != getpass.getpass("Confirm password: "):
            parser.error("Passwords must match and contain at least 12 characters")
        with db.write() as session:
            session.execute(delete(User))
            session.execute(delete(BrowserSession))
            session.add(User(name=args.username, password=password_hash(password)))
        print(f"Shared account {args.username!r} is ready.")
    elif args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(db), host=args.host, port=args.port)
    elif args.command == "import-qif":
        with db.write() as session:
            result = import_catalog(
                session, Path(args.path).read_text(encoding="utf-8-sig", errors="replace")
            )
        print(json.dumps({k: len(v) for k, v in result.items()}))
    elif args.command == "backup":
        target = Path(args.destination).resolve()
        if target.exists():
            parser.error("Choose a new backup directory")
        if db.directory == target or db.directory in target.parents:
            parser.error("Backup destination must be outside the live data directory")
        target.mkdir(parents=True, mode=0o700)
        with (
            sqlite3.connect(db.directory / "quicker.sqlite3") as source,
            sqlite3.connect(target / "quicker.sqlite3") as dest,
        ):
            source.backup(dest)
        # Originals are immutable and never deleted, so copying after the DB snapshot preserves every reference.
        shutil.copytree(db.blobs, target / "documents")
        print(f"Backup written to {target}")


if __name__ == "__main__":
    main()
