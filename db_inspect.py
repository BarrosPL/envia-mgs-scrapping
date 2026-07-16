from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError


IGNORED_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspeciona um PostgreSQL em uma transação somente leitura"
    )
    parser.add_argument("--schema", default="public", help="schema do PostgreSQL")
    parser.add_argument("--table", help="mostra colunas e algumas linhas desta tabela")
    parser.add_argument(
        "--limit", type=int, default=5, help="quantidade de linhas, entre 1 e 50"
    )
    parser.add_argument(
        "--list-schemas", action="store_true", help="lista os schemas disponíveis"
    )
    return parser.parse_args()


def load_database_url() -> str:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError(
            "DATABASE_URL não configurada. Copie .env.example para .env e preencha a conexão."
        )
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("DATABASE_URL deve apontar para um banco PostgreSQL")
    return database_url


def make_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


def json_default(value: Any) -> str:
    return str(value)


def print_connection_info(connection: Connection) -> None:
    row = connection.execute(
        text(
            "SELECT current_database() AS database, "
            "current_user AS username, inet_server_addr()::text AS host"
        )
    ).mappings().one()
    print("Conexão realizada com sucesso")
    print(f"Banco: {row['database']}")
    print(f"Usuário: {row['username']}")
    print(f"Servidor: {row['host'] or 'local/socket'}")


def list_schemas(engine: Engine) -> None:
    schemas = [
        name for name in inspect(engine).get_schema_names() if name not in IGNORED_SCHEMAS
    ]
    print("\nSchemas:")
    for name in sorted(schemas):
        print(f"- {name}")


def list_tables(engine: Engine, schema: str) -> None:
    inspector = inspect(engine)
    schemas = inspector.get_schema_names()
    if schema not in schemas:
        raise ValueError(f"schema não encontrado: {schema}")

    tables = inspector.get_table_names(schema=schema)
    views = inspector.get_view_names(schema=schema)
    print(f"\nTabelas em {schema}:")
    if not tables:
        print("(nenhuma tabela)")
    for name in sorted(tables):
        print(f"- {name}")
    if views:
        print(f"\nViews em {schema}:")
        for name in sorted(views):
            print(f"- {name}")


def inspect_table(connection: Connection, schema: str, table_name: str, limit: int) -> None:
    inspector = inspect(connection)
    if table_name not in inspector.get_table_names(schema=schema) and table_name not in inspector.get_view_names(
        schema=schema
    ):
        raise ValueError(f"tabela ou view não encontrada: {schema}.{table_name}")

    columns = inspector.get_columns(table_name, schema=schema)
    print(f"\nColunas de {schema}.{table_name}:")
    for column in columns:
        nullable = "aceita NULL" if column.get("nullable") else "obrigatória"
        print(f"- {column['name']}: {column['type']} ({nullable})")

    table = Table(table_name, MetaData(), schema=schema, autoload_with=connection)
    rows = connection.execute(select(table).limit(limit)).mappings()
    print(f"\nAmostra de até {limit} linhas:")
    found = False
    for row in rows:
        found = True
        print(json.dumps(dict(row), ensure_ascii=False, default=json_default))
    if not found:
        print("(tabela vazia)")


def main() -> int:
    args = parse_args()
    if not 1 <= args.limit <= 50:
        print("Erro: --limit deve estar entre 1 e 50", file=sys.stderr)
        return 2

    try:
        engine = make_engine(load_database_url())
        # A instrução SET protege inclusive consultas futuras adicionadas a este utilitário.
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                print_connection_info(connection)
                if args.list_schemas:
                    list_schemas(engine)
                if args.table:
                    inspect_table(connection, args.schema, args.table, args.limit)
                else:
                    list_tables(engine, args.schema)
            finally:
                transaction.rollback()
        engine.dispose()
        return 0
    except (ValueError, SQLAlchemyError) as exc:
        # Não imprime a URL de conexão para evitar expor a senha no terminal.
        print(f"Erro ao acessar o PostgreSQL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
