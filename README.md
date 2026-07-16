# Assistente local de DMs do Instagram

Abre um navegador visível, mantém a sessão em uma pasta local e ajuda a visitar uma lista de perfis e enviar uma mensagem. O login é sempre manual: a senha não é armazenada pelo programa.

> Use somente com pessoas que esperam seu contato. Automação em massa ou mensagens não solicitadas podem violar as regras do Instagram, gerar denúncias e bloquear a conta. Os seletores da interface também podem mudar a qualquer momento.

## Instalação (Windows / PowerShell)

É necessário ter Python 3.10 ou mais recente.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Uso

1. Edite `usernames.txt`, colocando um username por linha.
2. Faça primeiro uma simulação. Ela abre a conversa e preenche o texto, mas não envia:

```powershell
python app.py --message "Olá! Tudo bem?"
```

3. Confira o funcionamento com poucos perfis. Para habilitar envio real:

```powershell
python app.py --send --limit 3 --message "Olá! Tudo bem?"
```

O programa pede confirmação antes de cada envio. Existe `--yes` para dispensar essa pergunta, mas mantenha volumes baixos e use apenas em uma lista revisada.

Depois de processar a lista, o Chromium permanece aberto para você conferir o chat. Pressione Enter no terminal somente quando quiser fechá-lo. Para fechar automaticamente ao terminar, acrescente `--close-when-done`.

Para uma mensagem longa, salve-a como UTF-8 e use:

```powershell
python app.py --send --message-file mensagem.txt
```

O navegador reutiliza a sessão em `browser-data/`. Ao abrir, o programa aguarda 10 segundos para o Instagram carregar e inicia automaticamente, sem exigir Enter. Se a sessão tiver expirado, faça login na janela e execute novamente. O histórico fica em `envios.csv`; dados da sessão ficam em `browser-data/`. Ambos são ignorados pelo Git.

## Opções úteis

```text
--limit 15        máximo de perfis (limite rígido: 15 por execução)
--min-delay 45    espera mínima entre envios reais
--max-delay 90    espera máxima entre envios reais
--log arquivo.csv caminho do histórico
--close-when-done fecha o Chromium automaticamente ao terminar
```

Não execute duas instâncias ao mesmo tempo usando a mesma pasta `browser-data`.

O envio real também possui limite diário de 15 mensagens, calculado pelos registros com status `enviado` em `envios.csv`. Executar o programa novamente no mesmo dia utiliza apenas a cota restante. Simulações não consomem essa cota.

## Mensagens dinâmicas com IA a partir de Leads.xlsx

Configure estas variáveis no `.env`:

```env
OPENAI_API_KEY=sua_chave_da_openai
OPENAI_MODEL=gpt-5.6-luna
CUSTOMER_SERVICE_URL=https://seu-link-real-de-atendimento
```

O programa lê a aba `Leads Instagram`, usa `motivo_qualificacao` e `intencao_detectada` para gerar a mensagem e anexa o link de atendimento sem permitir que a IA o altere. Apenas registros qualificados, ainda não contatados e com confiança mínima são selecionados. Usernames com status `enviado` em `envios.csv` são ignorados.

Primeiro, simule com cinco leads. A API da OpenAI será chamada e poderá gerar custo, mas o Instagram não enviará a mensagem:

```powershell
python app.py --leads Leads.xlsx --limit 5
```

Depois de revisar o texto e o comportamento, habilite envio real. `--yes` retira a confirmação individual:

```powershell
python app.py --leads Leads.xlsx --limit 5 --send --yes
```

Por padrão, leads com confiança abaixo de 80 são ignorados. Para alterar o corte:

```powershell
python app.py --leads Leads.xlsx --min-confidence 90 --limit 5
```

## Inspecionar um banco PostgreSQL

Copie `.env.example` para `.env` e preencha `DATABASE_URL`. O arquivo `.env` é ignorado pelo Git e não deve ser compartilhado.

```powershell
Copy-Item .env.example .env
```

Para testar a conexão e listar as tabelas do schema `public`:

```powershell
python db_inspect.py
```

Para listar schemas ou examinar as colunas e até cinco linhas de uma tabela:

```powershell
python db_inspect.py --list-schemas
python db_inspect.py --schema public --table clientes --limit 5
```

O inspetor abre uma transação PostgreSQL em modo somente leitura e sempre executa rollback. Ainda assim, as linhas exibidas podem conter dados pessoais; evite compartilhar a saída completa.

## Produção no EasyPanel

O projeto pode operar como um worker Docker usando diretamente a tabela
`public.instagram_leads_qualificados`. Os envios são auditados em
`public.instagram_envios_log`, criada automaticamente. Apenas um worker consegue obter a trava
PostgreSQL; uma segunda instância encerra sem processar.

### 1. Criar o serviço

Crie um **App Service** a partir deste repositório. O EasyPanel detectará o `Dockerfile`.
Configure uma única réplica e monte um volume persistente:

```text
Nome do volume: instagram-data
Mount path: /data
```

Use a URL interna fornecida pelo serviço PostgreSQL, não o IP e a porta publicados na internet:

```env
DATABASE_URL=postgresql+psycopg://USUARIO:SENHA@HOST_INTERNO:5432/BANCO?sslmode=disable
OPENAI_API_KEY=...
OPENAI_MODEL=...
CUSTOMER_SERVICE_URL=https://...
TZ=America/Sao_Paulo
DAILY_SEND_LIMIT=15
BATCH_SIZE=15
MIN_CONFIDENCE=80
MIN_DELAY_SECONDS=45
MAX_DELAY_SECONDS=90
BROWSER_PROFILE_DIR=/data/browser-data
RUN_TIME=10:00
RUN_ON_START=false
DRY_RUN=true
```

Não copie o arquivo `.env` para a imagem e não coloque segredos no Git. Cadastre-os no campo
Environment do EasyPanel.

### 2. Fazer o primeiro login

Configure temporariamente:

```env
APP_MODE=login
VNC_PASSWORD=uma_senha_temporaria
```

Adicione um domínio HTTPS apontando para a porta `6080`, faça deploy e abra:

```text
https://SEU_DOMINIO/vnc.html?autoconnect=true&resize=scale
```

Digite a senha VNC, autentique-se no Instagram e confirme que a caixa de entrada abre. Em seguida,
remova o domínio público ou restrinja seu acesso. O perfil fica salvo em
`/data/browser-data`.

Nunca execute os modos `login` e `worker` ao mesmo tempo com o mesmo volume.

### 3. Testar sem enviar

Altere as variáveis e faça novo deploy:

```env
APP_MODE=worker
DRY_RUN=true
RUN_ON_START=true
```

Confira os logs. A simulação gera a mensagem e abre a conversa, mas não pressiona Enter. Depois do
teste, volte `RUN_ON_START=false` para que reinícios não iniciem lotes inesperadamente.

### 4. Ativar os envios

Depois de validar a simulação:

```env
APP_MODE=worker
DRY_RUN=false
RUN_ON_START=false
RUN_TIME=10:00
```

O agendador executa diariamente no fuso configurado. Para uma execução imediata controlada, use
`APP_MODE=worker`, `RUN_ON_START=true` e `BATCH_SIZE=1`. Após o teste, volte
`RUN_ON_START=false`. Evite `APP_MODE=once` em serviços com reinício automático.

Se o Instagram solicitar login, 2FA, CAPTCHA ou checkpoint, o worker encerra o lote sem marcar o
lead atual como contactado e retorna o código `3`. Troque temporariamente para `APP_MODE=login`,
restaure a sessão pelo noVNC e depois volte para `worker`.

`ALERT_WEBHOOK_URL` é opcional. Quando configurada, recebe JSON no formato
`{"text": "mensagem"}` em falhas ou quando a sessão precisa de intervenção.

### Segurança

- Remova a exposição pública do PostgreSQL depois de usar a rede interna do EasyPanel.
- Publique o noVNC somente durante a autenticação e use uma senha temporária.
- Mantenha apenas uma réplica do serviço.
- Faça backup do PostgreSQL; o volume contém apenas o perfil do navegador.
