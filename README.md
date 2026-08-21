# Ad Tracker — monitoramento diário de ofertas na Biblioteca de Anúncios

Ferramenta que substitui o trabalho manual de abrir cada link da Biblioteca
de Anúncios todo dia: roda sozinha 1x por dia, registra os criativos ativos
de cada oferta que você está rastreando, calcula o que é novo, o que foi
desativado e quais criativos estão sobrevivendo mais tempo (seu principal
sinal de "isso está funcionando"), e mostra tudo em um painel visual.

**Antes de configurar, leia:** este projeto automatiza a leitura da página
pública da Biblioteca de Anúncios (não existe API oficial da Meta para
anúncios comerciais fora da UE/Reino Unido). Isso significa duas coisas:
1. Os Termos de Serviço da Meta não autorizam coleta automatizada — o risco
   prático para este volume (poucas páginas, 1x/dia) tende a ser baixo, mas
   existe. Avalie você mesmo se topa esse risco.
2. A Meta muda o HTML da página com alguma frequência. Se um dia a coleta
   parar de retornar dados, é sinal de que os seletores em `scraper/scrape.py`
   precisam de ajuste (veja a seção "Se a coleta parar de funcionar" abaixo).

---

## Como funciona (visão geral)

```
GitHub Actions (roda 1x/dia)
        │
        ▼
scraper/scrape.py  ──►  abre cada link da Biblioteca de Anúncios
        │                 num navegador headless, extrai os anúncios
        ▼
docs/data/*.json   ──►  snapshots diários + diffs (novo/removido/sobrevivendo)
        │
        ▼
docs/index.html    ──►  painel visual (publicado via GitHub Pages)
```

Tudo roda de graça: GitHub Actions tem cota gratuita generosa para esse
volume de uso, e o GitHub Pages hospeda o painel sem custo.

---

## Passo a passo de configuração

### 1. Criar o repositório no GitHub
1. Crie uma conta gratuita em github.com, se ainda não tiver.
2. Crie um repositório novo (pode ser privado ou público — privado é
   recomendado, já que ele guarda os links das ofertas que você acompanha).
3. Suba todos os arquivos deste projeto para esse repositório (pelo site do
   GitHub mesmo, arrastando os arquivos, ou via `git push` se preferir linha
   de comando).

### 2. Ativar o GitHub Pages (o painel visual)
1. No repositório, vá em **Settings → Pages**.
2. Em "Source", selecione **Deploy from a branch**.
3. Branch: `main` (ou `master`), pasta: **/docs**.
4. Salve. Em alguns minutos o painel estará disponível em uma URL do tipo
   `https://SEU-USUARIO.github.io/NOME-DO-REPO/`.

### 3. Conferir se as Actions estão habilitadas
1. Vá na aba **Actions** do repositório.
2. Se aparecer um aviso pedindo para habilitar workflows, clique para
   habilitar. O workflow `Coleta diária da Biblioteca de Anúncios` já está
   configurado para rodar todo dia às 09:00 (horário de Brasília).

### 4. Adicionar suas ofertas
Edite o arquivo `docs/data/offers.json` (direto pelo site do GitHub: abra o
arquivo → ícone de lápis → editar) e substitua pelo formato:

```json
{
  "offers": [
    {
      "name": "Nome que você quiser dar pra oferta A",
      "url": "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&view_all_page_id=123456789&search_type=page&media_type=all",
      "active": true
    },
    {
      "name": "Nome da oferta B",
      "url": "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&view_all_page_id=987654321&search_type=page&media_type=all",
      "active": true
    }
  ]
}
```

**Como pegar a URL certa:** abra a Biblioteca de Anúncios normalmente
(como você já faz hoje), filtre pela página do anunciante que quer
rastrear com o status "Ativos" e o país certo, e copie a URL da barra de
endereço — é exatamente essa URL que vai no campo `"url"`.

Salve o arquivo (commit direto na branch `main`). Na próxima execução do
workflow, essa oferta já entra na coleta.

### 5. Testar manualmente (não precisa esperar o horário agendado)
Na aba **Actions** → clique no workflow "Coleta diária da Biblioteca de
Anúncios" → botão **Run workflow**. Acompanhe o log em tempo real — ele
mostra quantos anúncios foram encontrados por oferta.

### 6. Ver o painel
Acesse a URL do GitHub Pages (passo 2). No primeiro dia você só vai ver a
contagem de ativos (ainda não há "ontem" pra comparar). A partir do segundo
dia de coleta, os números de novos/removidos/sobreviventes começam a
aparecer.

---

## Rodando localmente (opcional, útil pra testar/depurar)

```bash
cd scraper
pip install -r requirements.txt
playwright install --with-deps chromium
python scrape.py
```

Para depurar quando algo não estiver sendo extraído corretamente, rode com
`DEBUG=1` — isso salva o texto bruto de cada card de anúncio em
`scraper/debug/`, o que ajuda a entender o que mudou na página:

```bash
DEBUG=1 python scrape.py
```

## Se a coleta parar de funcionar

Sinais de que a Meta mudou algo na página:
- O log do GitHub Actions mostra "0 ativos" para ofertas que você sabe que
  têm anúncios rodando.
- O arquivo de debug (`scraper/debug/`) mostra texto estranho ou vazio.

O que ajustar em `scraper/scrape.py`:
- `LIBRARY_ID_PATTERNS` / `STARTED_PATTERNS`: expressões regulares que
  procuram por "Library ID:" e "Started running on" (ou as versões em
  português). Se a Meta mudar esses textos, é só atualizar os padrões.
- `dismiss_cookie_banner`: lista de textos de botão de cookies — se a Meta
  mudar o texto do botão, adicione o novo texto na lista.

## Limitações conhecidas

- Como não há API oficial para anúncios comerciais fora da UE/Reino Unido,
  este projeto lê a página pública — não há dados de investimento (spend)
  ou impressões, os mesmos que você já não tinha acesso navegando manualmente.
- O texto extraído do criativo (`snippet`) é uma heurística — pode não ser
  perfeito para todos os formatos de anúncio (especialmente carrosséis).
- Se uma oferta tiver muitos anúncios (100+), a coleta demora mais — o
  timeout do workflow está em 30 minutos, ajuste em
  `.github/workflows/daily-scrape.yml` se precisar.
