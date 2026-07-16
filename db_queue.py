from __future__ import annotations

import os
from contextlib import AbstractContextManager
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import Connection, Engine, create_engine, text

from leads_ai import Lead


LOCK_ID = 726_491_305


def database_url() -> str:
    load_dotenv()
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise ValueError("DATABASE_URL não configurada")
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def create_database_engine() -> Engine:
    return create_engine(
        database_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


class DatabaseLeadStore(AbstractContextManager["DatabaseLeadStore"]):
    def __init__(self, engine: Engine):
        self.engine = engine
        self.connection: Connection | None = None
        self.locked = False

    def __enter__(self) -> "DatabaseLeadStore":
        self.connection = self.engine.connect()
        self.ensure_schema()
        self.locked = bool(
            self.connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": LOCK_ID}
            ).scalar_one()
        )
        self.connection.commit()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.connection is not None:
            if self.locked:
                self.connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": LOCK_ID}
                )
                self.connection.commit()
            self.connection.close()
        self.engine.dispose()

    def _connection(self) -> Connection:
        if self.connection is None:
            raise RuntimeError("DatabaseLeadStore não foi aberto")
        return self.connection

    def ensure_schema(self) -> None:
        connection = self._connection()
        with connection.begin():
            connection.execute(
                text(
                    """
                    ALTER TABLE public.instagram_leads_qualificados
                    ADD COLUMN IF NOT EXISTS leads_ja_contactados
                    BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS public.instagram_envios_log (
                        id BIGSERIAL PRIMARY KEY,
                        lead_id BIGINT,
                        instagram_username VARCHAR(255) NOT NULL,
                        status VARCHAR(50) NOT NULL,
                        detalhe TEXT,
                        mensagem TEXT,
                        criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_instagram_envios_log_status_data
                    ON public.instagram_envios_log (status, criado_em)
                    """
                )
            )

    def sent_today(self) -> int:
        value = int(
            self._connection().execute(
                text(
                    """
                    SELECT count(*) FROM public.instagram_envios_log
                    WHERE status = 'enviado'
                      AND (criado_em AT TIME ZONE :timezone)::date =
                          (now() AT TIME ZONE :timezone)::date
                    """
                ),
                {"timezone": os.getenv("TZ", "America/Sao_Paulo")},
            ).scalar_one()
        )
        self._connection().commit()
        return value

    def load_pending(self, limit: int, min_confidence: float) -> list[Lead]:
        rows = list(self._connection().execute(
            text(
                """
                SELECT id, instagram_username, motivo_qualificacao,
                       intencao_detectada, confianca_ia
                FROM public.instagram_leads_qualificados
                WHERE NOT leads_ja_contactados
                  AND COALESCE(contato_realizado, FALSE) = FALSE
                  AND confianca_ia >= :min_confidence
                  AND NULLIF(trim(motivo_qualificacao), '') IS NOT NULL
                  AND NULLIF(trim(instagram_username), '') IS NOT NULL
                  AND COALESCE(status_contato, '') <> 'nao_encontrado'
                ORDER BY qualificado_em NULLS LAST, id
                LIMIT :limit
                """
            ),
            {"limit": limit, "min_confidence": min_confidence},
        ).mappings())
        self._connection().commit()
        return [
            Lead(
                lead_id=str(row["id"]),
                username=str(row["instagram_username"]).strip().removeprefix("@").lower(),
                qualification_reason=str(row["motivo_qualificacao"]),
                intent=str(row["intencao_detectada"] or ""),
                confidence=float(row["confianca_ia"]),
            )
            for row in rows
        ]

    def record(self, lead: Lead, status: str, detail: str, message: str = "") -> None:
        connection = self._connection()
        with connection.begin():
            connection.execute(
                text(
                    """
                    INSERT INTO public.instagram_envios_log
                        (lead_id, instagram_username, status, detalhe, mensagem)
                    VALUES (:lead_id, :username, :status, :detail, :message)
                    """
                ),
                {
                    "lead_id": int(lead.lead_id),
                    "username": lead.username,
                    "status": status,
                    "detail": detail or None,
                    "message": message or None,
                },
            )
            if status == "enviado":
                connection.execute(
                    text(
                        """
                        UPDATE public.instagram_leads_qualificados
                        SET leads_ja_contactados = TRUE,
                            contato_realizado = TRUE,
                            status_contato = 'enviado',
                            mensagem_sugerida = :message,
                            atualizado_em = now()
                        WHERE id = :lead_id
                        """
                    ),
                    {"lead_id": int(lead.lead_id), "message": message},
                )
            else:
                connection.execute(
                    text(
                        """
                        UPDATE public.instagram_leads_qualificados
                        SET status_contato = :status,
                            mensagem_sugerida = COALESCE(NULLIF(:message, ''), mensagem_sugerida),
                            atualizado_em = now()
                        WHERE id = :lead_id
                        """
                    ),
                    {"lead_id": int(lead.lead_id), "status": status, "message": message},
                )
