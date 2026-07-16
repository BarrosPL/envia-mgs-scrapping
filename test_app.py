from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from app import count_sent_today, load_usernames, normalize_username
from leads_ai import Lead, OpenAIMessageGenerator, load_leads, load_sent_usernames
from scheduler import next_run


class UsernameTests(unittest.TestCase):
    def test_normalize_username(self):
        self.assertEqual(normalize_username(" @Nome.LoJa "), "nome.loja")
        self.assertIsNone(normalize_username("# comentário"))
        self.assertIsNone(normalize_username("  "))

    def test_rejects_invalid_username(self):
        with self.assertRaises(ValueError):
            normalize_username("nome com espaço")

    def test_load_usernames_removes_duplicates(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "usernames.txt"
            source.write_text("@Alpha\nbeta\nALPHA\n", encoding="utf-8")
            self.assertEqual(load_usernames(source), ["alpha", "beta"])

    def test_counts_only_successful_sends_from_today(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "envios.csv"
            source.write_text(
                "data,username,status,detalhe\n"
                "2026-07-13T10:00:00-03:00,alpha,enviado,\n"
                "2026-07-13T11:00:00-03:00,beta,erro,\n"
                "2026-07-12T10:00:00-03:00,gamma,enviado,\n",
                encoding="utf-8",
            )
            now = datetime.fromisoformat("2026-07-13T20:00:00-03:00")
            self.assertEqual(count_sent_today(source, now), 1)


class LeadsTests(unittest.TestCase):
    def test_loads_only_eligible_leads(self):
        headers = [
            "id",
            "instagram_username",
            "motivo_qualificacao",
            "intencao_detectada",
            "confianca_ia",
            "qualificado",
            "contato_realizado",
        ]
        with TemporaryDirectory() as directory:
            source = Path(directory) / "leads.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Leads Instagram"
            sheet.append(headers)
            sheet.append([1, "@alpha", "Demonstrou interesse", "cidadania", 92, True, False])
            sheet.append([2, "beta", "Já foi atendido", "visto", 95, True, True])
            sheet.append([3, "gamma", "Confiança baixa", "visto", 70, True, False])
            workbook.save(source)

            leads = load_leads(source, min_confidence=80)
            self.assertEqual([lead.username for lead in leads], ["alpha"])
            self.assertEqual(leads[0].qualification_reason, "Demonstrou interesse")

    def test_reads_sent_usernames(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "envios.csv"
            source.write_text(
                "data,username,status,detalhe\n2026-01-01,@Alpha,enviado,\n",
                encoding="utf-8",
            )
            self.assertEqual(load_sent_usernames(source), {"alpha"})

    def test_generator_appends_only_configured_link(self):
        class FakeResponse:
            output_text = "Em uma de nossas pesquisas, identificamos seu interesse em cidadania."

        class FakeResponses:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClient:
            responses = FakeResponses()

        generator = OpenAIMessageGenerator(
            "test-key", "https://atendimento.example", "test-model"
        )
        generator.client = FakeClient()
        message = generator.generate(Lead("1", "alpha", "Interesse", "cidadania", 90))
        self.assertIn("https://atendimento.example", message)
        self.assertIn("conversar com calma", message)
        self.assertEqual(message.count("https://"), 1)


class SchedulerTests(unittest.TestCase):
    def test_next_run_uses_today_when_time_is_in_future(self):
        tz = ZoneInfo("America/Sao_Paulo")
        now = datetime(2026, 7, 16, 9, 0, tzinfo=tz)
        self.assertEqual(next_run(now, "10:30"), datetime(2026, 7, 16, 10, 30, tzinfo=tz))

    def test_next_run_uses_tomorrow_after_scheduled_time(self):
        tz = ZoneInfo("America/Sao_Paulo")
        now = datetime(2026, 7, 16, 11, 0, tzinfo=tz)
        self.assertEqual(next_run(now, "10:30"), datetime(2026, 7, 17, 10, 30, tzinfo=tz))


if __name__ == "__main__":
    unittest.main()
