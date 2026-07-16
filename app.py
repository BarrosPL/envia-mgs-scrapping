from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from db_queue import DatabaseLeadStore, create_database_engine
from leads_ai import AIMessageError, Lead, OpenAIMessageGenerator, load_leads, load_sent_usernames
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


BASE_URL = "https://www.instagram.com"
DEFAULT_PROFILE_DIR = Path("browser-data")
DEFAULT_LOG_FILE = Path("envios.csv")
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "15"))
STARTUP_WAIT_MS = 10_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


@dataclass(frozen=True)
class Result:
    username: str
    status: str
    detail: str = ""


def normalize_username(value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    value = value.removeprefix("@").strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError(f"username inválido: {value!r}")
    return value.lower()


def load_usernames(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"arquivo não encontrado: {path}")

    result: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        try:
            username = normalize_username(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if username and username not in seen:
            seen.add(username)
            result.append(username)
    return result


def append_log(path: Path, result: Result) -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if new_file:
            writer.writerow(["data", "username", "status", "detalhe"])
        writer.writerow(
            [datetime.now().astimezone().isoformat(timespec="seconds"), result.username, result.status, result.detail]
        )


def count_sent_today(path: Path, now: datetime | None = None) -> int:
    if not path.exists():
        return 0
    today = (now or datetime.now().astimezone()).astimezone().date()
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if str(row.get("status") or "").strip().lower() != "enviado":
                continue
            try:
                sent_at = datetime.fromisoformat(str(row.get("data") or ""))
                if sent_at.tzinfo is None:
                    sent_at = sent_at.astimezone()
                if sent_at.astimezone().date() == today:
                    count += 1
            except ValueError:
                continue
    return count


def navigate(page: Page, url: str, attempts: int = 3) -> None:
    """Navega tolerando os redirecionamentos automáticos do Instagram."""
    last_error: PlaywrightError | None = None
    for attempt in range(attempts):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PlaywrightTimeoutError:
            pass

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            return
        except PlaywrightError as exc:
            last_error = exc
            if "interrupted by another navigation" not in str(exc).lower():
                raise
            page.wait_for_timeout(1500 * (attempt + 1))

    if last_error:
        raise last_error


def authentication_problem(page: Page) -> str | None:
    url = page.url.lower()
    if "/accounts/login" in url:
        return "sessão expirada"
    if "/challenge/" in url or "/checkpoint/" in url:
        return "checkpoint do Instagram detectado"
    challenge = first_visible(
        page,
        [
            'text="Confirme que é você"',
            'text="Confirm it\'s you"',
            'text="Insira o código de segurança"',
            'text="Enter security code"',
            'text="captcha"',
        ],
    )
    return "verificação manual do Instagram necessária" if challenge else None


def validate_session(page: Page) -> None:
    navigate(page, BASE_URL)
    print("\nAguardando 10 segundos para o Instagram e a sessão carregarem...")
    page.wait_for_timeout(STARTUP_WAIT_MS)
    navigate(page, f"{BASE_URL}/direct/inbox/")
    problem = authentication_problem(page)
    if problem:
        raise RuntimeError(f"{problem}; abra o serviço em modo login")
    print("Sessão carregada. Iniciando o processamento automaticamente.")


def first_visible(page: Page, selectors: Iterable[str]):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                return locator.first
        except PlaywrightTimeoutError:
            continue
    return None


def dismiss_dialogs(page: Page) -> None:
    button = first_visible(
        page,
        [
            'button:has-text("Agora não")',
            'button:has-text("Not now")',
            'button:has-text("Recusar cookies opcionais")',
            'button:has-text("Decline optional cookies")',
        ],
    )
    if button:
        button.click()
        page.wait_for_timeout(500)


def open_message_from_options_menu(page: Page) -> bool:
    options_button = first_visible(
        page,
        [
            'button:has(svg[aria-label="Opções"])',
            'div[role="button"]:has(svg[aria-label="Opções"])',
            'svg[aria-label="Opções"]',
            'button:has(svg[aria-label="Options"])',
            'div[role="button"]:has(svg[aria-label="Options"])',
            'svg[aria-label="Options"]',
            'button[aria-label="Opções"]',
            'button[aria-label="Options"]',
            'button[aria-label="Mais opções"]',
            'button[aria-label="More options"]',
        ],
    )
    if not options_button:
        return False

    options_button.click()
    page.wait_for_timeout(700)
    send_message_item = first_visible(
        page,
        [
            'div[role="dialog"] button:has-text("Enviar mensagem")',
            'div[role="dialog"] div[role="button"]:has-text("Enviar mensagem")',
            'div[role="dialog"] button:has-text("Send message")',
            'div[role="dialog"] div[role="button"]:has-text("Send message")',
            'button:has-text("Enviar mensagem")',
            'div[role="button"]:has-text("Enviar mensagem")',
            'button:has-text("Send message")',
            'div[role="button"]:has-text("Send message")',
            'text="Enviar mensagem"',
            'text="Send message"',
        ],
    )
    if not send_message_item:
        page.keyboard.press("Escape")
        return False

    send_message_item.click()
    return True


def open_conversation(page: Page, username: str) -> Result | None:
    navigate(page, f"{BASE_URL}/{username}/")
    page.wait_for_timeout(1800)
    dismiss_dialogs(page)

    problem = authentication_problem(page)
    if problem:
        return Result(username, "sessao_expirada", problem)

    unavailable = first_visible(
        page,
        [
            'text="Esta página não está disponível."',
            'text="Sorry, this page isn\'t available."',
            'text="Página não encontrada"',
        ],
    )
    if unavailable:
        return Result(username, "nao_encontrado", "perfil indisponível")

    message_button = first_visible(
        page,
        [
            'div[role="button"]:has-text("Mensagem")',
            'div[role="button"]:has-text("Message")',
            'button:has-text("Mensagem")',
            'button:has-text("Message")',
        ],
    )
    if message_button:
        message_button.click()
    elif not open_message_from_options_menu(page):
        return Result(username, "erro", "botão de mensagem e menu de opções não encontrados")

    try:
        page.wait_for_url(re.compile(r"instagram\.com/direct/"), timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1500)
    return None


def type_message(page: Page, message: str) -> bool:
    composer = first_visible(
        page,
        [
            'div[contenteditable="true"][role="textbox"]',
            'textarea[placeholder*="Mensagem"]',
            'textarea[placeholder*="Message"]',
            'textarea',
        ],
    )
    if not composer:
        return False
    composer.click()
    composer.fill(message)
    return True


def confirm_send(username: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    answer = input(f"Enviar para @{username}? [s/N] ").strip().lower()
    return answer in {"s", "sim", "y", "yes"}


def process_user(page: Page, username: str, message: str, send: bool, assume_yes: bool) -> Result:
    error = open_conversation(page, username)
    if error:
        return error

    if not type_message(page, message):
        return Result(username, "erro", "campo de mensagem não encontrado")

    if not send:
        return Result(username, "simulado", "mensagem preenchida, mas não enviada")
    if not confirm_send(username, assume_yes):
        return Result(username, "ignorado", "envio não confirmado")

    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    return Result(username, "enviado")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assistente local para DMs individuais no Instagram")
    parser.add_argument("--usernames", type=Path, default=Path("usernames.txt"))
    parser.add_argument("--leads", type=Path, help="planilha XLSX usada para gerar mensagens com IA")
    parser.add_argument("--database", action="store_true", help="lê e atualiza leads no PostgreSQL")
    parser.add_argument("--sheet", default="Leads Instagram", help="aba da planilha de leads")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=80,
        help="confiança mínima da qualificação para usar o lead (padrão: 80)",
    )
    parser.add_argument("--message", help="texto da mensagem")
    parser.add_argument("--message-file", type=Path, help="arquivo UTF-8 contendo a mensagem")
    parser.add_argument("--send", action="store_true", help="habilita envio real; sem esta opção é simulação")
    parser.add_argument("--yes", action="store_true", help="não pergunta antes de cada envio (requer --send)")
    parser.add_argument("--limit", type=int, default=15, help="máximo de perfis nesta execução (padrão: 15)")
    parser.add_argument("--min-delay", type=int, default=45, help="espera mínima entre envios, em segundos")
    parser.add_argument("--max-delay", type=int, default=90, help="espera máxima entre envios, em segundos")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes"},
        help="executa o Chromium sem interface gráfica",
    )
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_FILE)
    parser.add_argument(
        "--close-when-done",
        action="store_true",
        help="fecha o navegador automaticamente ao terminar",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> str | None:
    if args.database and args.leads:
        raise ValueError("não use --database e --leads juntos")
    if args.leads or args.database:
        if args.message or args.message_file:
            raise ValueError("não use mensagem fixa junto com a fonte de leads")
    elif bool(args.message) == bool(args.message_file):
        raise ValueError("informe exatamente um de --message ou --message-file")
    if args.yes and not args.send:
        raise ValueError("--yes só pode ser usado junto com --send")
    if not 1 <= args.limit <= DAILY_SEND_LIMIT:
        raise ValueError(f"--limit deve estar entre 1 e {DAILY_SEND_LIMIT}")
    if args.min_delay < 20 or args.max_delay < args.min_delay:
        raise ValueError("use atraso mínimo de 20 segundos e máximo maior ou igual ao mínimo")
    if not 0 <= args.min_confidence <= 100:
        raise ValueError("--min-confidence deve estar entre 0 e 100")
    if args.leads or args.database:
        return None
    message = args.message or args.message_file.read_text(encoding="utf-8").strip()
    if not message:
        raise ValueError("a mensagem está vazia")
    return message


def run(args: argparse.Namespace, store: DatabaseLeadStore | None = None) -> int:
    try:
        fixed_message = validate_args(args)
        effective_limit = args.limit
        sent_today = (store.sent_today() if store else count_sent_today(args.log)) if args.send else 0
        if args.send:
            remaining_today = max(0, DAILY_SEND_LIMIT - sent_today)
            if remaining_today == 0:
                print(
                    f"Limite diário atingido: {sent_today}/{DAILY_SEND_LIMIT} mensagens enviadas hoje."
                )
                return 0
            effective_limit = min(effective_limit, remaining_today)
        generator: OpenAIMessageGenerator | None = None
        leads_by_username: dict[str, Lead] = {}
        if args.database:
            if store is None or not store.locked:
                print("Outra execução do worker já está ativa; encerrando sem processar.")
                return 0
            leads = store.load_pending(effective_limit, args.min_confidence)
            leads_by_username = {lead.username: lead for lead in leads}
            usernames = [lead.username for lead in leads]
            generator = OpenAIMessageGenerator.from_env()
        elif args.leads:
            sent = load_sent_usernames(args.log)
            leads = [
                lead
                for lead in load_leads(args.leads, args.sheet, args.min_confidence)
                if lead.username not in sent
            ][:effective_limit]
            leads_by_username = {lead.username: lead for lead in leads}
            usernames = [lead.username for lead in leads]
            generator = OpenAIMessageGenerator.from_env()
        else:
            usernames = load_usernames(args.usernames)[:effective_limit]
    except (OSError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    if not usernames:
        print("Nenhum lead pendente elegível foi encontrado.")
        return 0

    print(f"Perfis nesta execução: {len(usernames)}")
    print("Modo:", "ENVIO REAL" if args.send else "SIMULAÇÃO")
    if args.send:
        print(
            f"Cota diária antes desta execução: {sent_today}/{DAILY_SEND_LIMIT}; "
            f"disponíveis: {DAILY_SEND_LIMIT - sent_today}"
        )

    args.profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.profile_dir.resolve()),
            headless=args.headless,
            viewport={"width": 1280, "height": 850},
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            validate_session(page)
            for index, username in enumerate(usernames):
                print(f"\n[{index + 1}/{len(usernames)}] @{username}")
                if generator:
                    try:
                        message = generator.generate(leads_by_username[username])
                        print(f"Mensagem gerada:\n{message}\n")
                    except AIMessageError as exc:
                        result = Result(username, "erro_ia", str(exc))
                        if store:
                            store.record(leads_by_username[username], result.status, result.detail)
                        else:
                            append_log(args.log, result)
                        print(f"Resultado: {result.status} — {result.detail}")
                        continue
                else:
                    message = fixed_message or ""
                result = process_user(page, username, message, args.send, args.yes)
                if store:
                    store.record(leads_by_username[username], result.status, result.detail, message)
                else:
                    append_log(args.log, result)
                print(f"Resultado: {result.status}" + (f" — {result.detail}" if result.detail else ""))
                if result.status == "sessao_expirada":
                    print("Execução pausada: autenticação manual necessária.", file=sys.stderr)
                    return 3
                if index < len(usernames) - 1 and result.status == "enviado":
                    delay = random.randint(args.min_delay, args.max_delay)
                    print(f"Aguardando {delay}s antes do próximo perfil...")
                    time.sleep(delay)
        except RuntimeError as exc:
            print(f"Autenticação necessária: {exc}", file=sys.stderr)
            return 3
        except KeyboardInterrupt:
            print("\nExecução interrompida pelo usuário.")
        finally:
            if not args.close_when_done and not page.is_closed():
                print("\nProcessamento concluído. O Chromium continuará aberto.")
                try:
                    input("Pressione Enter no terminal quando quiser fechar o navegador...")
                except (EOFError, KeyboardInterrupt):
                    pass
            context.close()
    return 0


def main() -> int:
    args = parse_args()
    if not args.database:
        return run(args)
    try:
        with DatabaseLeadStore(create_database_engine()) as store:
            return run(args, store)
    except Exception as exc:
        print(f"Erro no PostgreSQL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
