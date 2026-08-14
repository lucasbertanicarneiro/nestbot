"""UI local de importacao: PDF/imagem -> rascunho editavel -> data/knowledge/*.md.

Gate de revisao humana antes de qualquer conteudo extraido automaticamente
virar parte da base -- nunca escreve em data/knowledge/ sem aprovacao manual.
Dev-only: nunca sobe em producao (fica atras do profile "import" no
docker-compose.yml, e do bind em 127.0.0.1).

Fluxo:
    POST /extrair       -> extrai PDF/imagem, grava rascunho, redireciona
    GET  /revisar/{id}  -> form de edicao pre-preenchido pelo rascunho
    POST /aprovar/{id}  -> grava .md final em data/knowledge/, apaga rascunho
    POST /descartar/{id}-> apaga rascunho sem gravar nada
"""
from __future__ import annotations

import base64
import html
import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path

import frontmatter
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from . import extracao, llm, prompts

log = logging.getLogger(__name__)

app = FastAPI(title="Importador Henri")

DIR_CONHECIMENTO = Path(__file__).resolve().parent.parent / "data" / "knowledge"
DIR_RASCUNHOS = Path(__file__).resolve().parent.parent / "data" / ".rascunhos"

# Pseudo-categorias de roteamento (prompts.CATEGORIAS) nao fazem sentido num documento.
CATEGORIAS_DOC = [c for c in prompts.CATEGORIAS if c not in ("saudacao", "despedida", "fora_de_escopo")]

EXTENSOES_IMAGEM = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_ID_RASCUNHO = re.compile(r"^[0-9a-f]{32}$")


def _esc(valor: object) -> str:
    return html.escape(str(valor), quote=True)


# ------------------------------------------------------------------
# Helpers de arquivo/slug
# ------------------------------------------------------------------

def _slugificar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", sem_acento).strip("-").lower()
    return slug or "documento"


def _proximo_prefixo() -> str:
    maior = 0
    for caminho in DIR_CONHECIMENTO.glob("*.md"):
        m = re.match(r"^(\d+)-", caminho.stem)
        if m:
            maior = max(maior, int(m.group(1)))
    return f"{maior + 1:02d}"


def _fontes_existentes() -> list[str]:
    fontes = set()
    for caminho in DIR_CONHECIMENTO.glob("*.md"):
        fonte = frontmatter.load(caminho).metadata.get("fonte")
        if fonte:
            fontes.add(str(fonte))
    return sorted(fontes)


def _arquivo_existente_por_titulo_fonte(titulo: str, fonte: str) -> Path | None:
    """Mesma chave que ingerir_arquivo usa pra decidir novo-vs-atualizacao."""
    for caminho in DIR_CONHECIMENTO.glob("*.md"):
        meta = frontmatter.load(caminho).metadata
        if meta.get("titulo") == titulo and meta.get("fonte") == fonte:
            return caminho
    return None


# ------------------------------------------------------------------
# Rascunhos (data/.rascunhos/<uuid>.json)
# ------------------------------------------------------------------

def _validar_id(id_: str) -> None:
    if not _ID_RASCUNHO.match(id_):
        raise HTTPException(404, "Rascunho invalido.")


def _salvar_rascunho(dados: dict) -> str:
    DIR_RASCUNHOS.mkdir(parents=True, exist_ok=True)
    id_ = uuid.uuid4().hex
    (DIR_RASCUNHOS / f"{id_}.json").write_text(
        json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return id_


def _carregar_rascunho(id_: str) -> dict | None:
    caminho = DIR_RASCUNHOS / f"{id_}.json"
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def _apagar_rascunho(id_: str) -> None:
    (DIR_RASCUNHOS / f"{id_}.json").unlink(missing_ok=True)


# ------------------------------------------------------------------
# HTML (f-string puro -- sem Jinja2, projeto nao tem frontend)
# ------------------------------------------------------------------

def _layout(titulo_pagina: str, corpo: str) -> HTMLResponse:
    html_doc = f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(titulo_pagina)} - Importador Henri</title>
<style>
  :root {{
    --vinho: #7a1030;
    --vinho-escuro: #56091f;
    --creme: #faf6f0;
    --tinta: #2b2420;
    --tinta-suave: #6b6259;
    --borda: #e4dcd0;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; max-width: 820px; margin: 0 auto;
         padding: 0 1rem 3rem; color: var(--tinta); line-height: 1.5; background: var(--creme); }}
  header.topo {{ border-bottom: 3px solid var(--vinho); padding: 1.75rem 0 1rem;
    margin-bottom: 1.75rem; }}
  header.topo .marca {{ font-size: .78rem; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: var(--vinho); }}
  header.topo h1 {{ font-size: 1.4rem; margin: .2rem 0 0; color: var(--tinta); }}
  .cartao {{ background: #fff; border: 1px solid var(--borda); border-radius: 10px;
    padding: 1.5rem 1.75rem; box-shadow: 0 1px 3px rgba(43,36,32,.06); }}
  label {{ display: block; margin-top: 1rem; font-weight: 600; }}
  input[type=text], input[type=url], select, textarea {{
    width: 100%; padding: .55rem .6rem; font: inherit; box-sizing: border-box;
    border: 1px solid var(--borda); border-radius: 6px; margin-top: .3rem; background: var(--creme);
  }}
  input:focus, select:focus, textarea:focus {{
    outline: none; border-color: var(--vinho); background: #fff;
  }}
  textarea {{ min-height: 320px; font-family: ui-monospace, monospace; font-size: .9rem; }}
  button {{ margin-top: 1.25rem; margin-right: .5rem; padding: .6rem 1.3rem; font: inherit;
    font-weight: 600; border-radius: 6px; border: 1px solid var(--vinho); background: var(--vinho);
    color: #fff; cursor: pointer; }}
  button:hover {{ background: var(--vinho-escuro); border-color: var(--vinho-escuro); }}
  button.secundario {{ background: transparent; color: var(--vinho); }}
  button.secundario:hover {{ background: rgba(122,16,48,.08); }}
  a {{ color: var(--vinho); }}
  .aviso {{ background: #fdf1de; border: 1px solid #f0d9a8; padding: .75rem;
    border-radius: 6px; margin-top: 1rem; }}
  .erro {{ background: #fbe4e4; border: 1px solid #eab3b3; padding: .75rem;
    border-radius: 6px; margin-top: 1rem; }}
  pre {{ background: var(--creme); border: 1px solid var(--borda); padding: .75rem;
    border-radius: 6px; overflow-x: auto; }}
  .campo-arquivo {{ border: 2px dashed var(--vinho); border-radius: 8px; padding: 2rem;
    text-align: center; margin-top: .5rem; background: var(--creme); }}
  p {{ color: var(--tinta-suave); }}
</style>
</head>
<body>
<header class="topo">
  <div class="marca">Henri &middot; Programa de Trainee</div>
  <h1>Importador de conhecimento</h1>
</header>
<div class="cartao">
{corpo}
</div>
</body>
</html>"""
    return HTMLResponse(html_doc)


# ------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------

@app.get("/")
def index() -> HTMLResponse:
    corpo = """
<p>Envie PDF ou print/imagem. O conteudo extraido vira um rascunho editavel
antes de entrar na base &mdash; nada e gravado sem sua revisao.</p>
<form action="/extrair" method="post" enctype="multipart/form-data">
  <div class="campo-arquivo">
    <input type="file" name="arquivos" multiple accept=".pdf,.png,.jpg,.jpeg,.webp" required>
    <p style="font-size:.9rem;">PDF com texto nativo ou imagem (print de tela).
    Varias imagens do mesmo documento (ex: print rolado)? Selecione todas de uma vez.</p>
  </div>
  <label style="font-weight:400; margin-top:.75rem;">
    <input type="checkbox" name="forcar_visao_pdf" value="true">
    Forcar extracao de PDF por imagem
  </label>
  <p style="font-size:.85rem; margin-top:.15rem;">
    Normalmente nao precisa mexer nisso: PDF com texto solto/picotado
    (infografico, slide) ou escaneado cai automaticamente na extracao por
    imagem. So marque se o texto sair errado mesmo assim.
  </p>
  <button type="submit">Extrair</button>
</form>
"""
    return _layout("Importar", corpo)


@app.post("/extrair")
async def extrair(
    arquivos: list[UploadFile] = File(...),
    forcar_visao_pdf: str | None = Form(None),
) -> RedirectResponse:
    usar_visao_forcado = bool(forcar_visao_pdf)
    partes: list[str] = []
    avisos: list[str] = []
    nomes: list[str] = []

    for arquivo in arquivos:
        nome = arquivo.filename or "arquivo"
        nomes.append(nome)
        sufixo = Path(nome).suffix.lower()
        dados = await arquivo.read()

        try:
            if sufixo == ".pdf":
                resultado = extracao.extrair_texto_pdf(dados)
                usar_visao = resultado.precisa_fallback_visual or usar_visao_forcado

                if not usar_visao and resultado.texto:
                    partes.append(resultado.texto)

                if usar_visao:
                    if resultado.aviso:
                        avisos.append(f"{nome}: {resultado.aviso}")
                    try:
                        paginas = extracao.pdf_para_imagens(dados)
                    except Exception as e:
                        log.exception("Falha renderizando paginas de %s", nome)
                        avisos.append(f"{nome}: falha ao renderizar paginas -- {e}")
                        paginas = []
                    for i, pagina_png in enumerate(paginas, start=1):
                        b64 = base64.b64encode(pagina_png).decode("ascii")
                        try:
                            resposta = llm.transcrever_imagem(b64, "image/png")
                            if resposta.texto:
                                partes.append(resposta.texto)
                        except Exception as e:
                            log.exception("Falha na visao, %s pagina %d", nome, i)
                            avisos.append(f"{nome} pagina {i}: falha na extracao -- {e}")
            elif sufixo in EXTENSOES_IMAGEM:
                b64 = base64.b64encode(dados).decode("ascii")
                resposta = llm.transcrever_imagem(b64, EXTENSOES_IMAGEM[sufixo])
                if resposta.texto:
                    partes.append(resposta.texto)
            else:
                avisos.append(f"{nome}: formato nao suportado, ignorado.")
        except Exception as e:
            log.exception("Falha extraindo %s", nome)
            avisos.append(f"{nome}: falha na extracao -- {e}")

    corpo_extraido = "\n\n---\n\n".join(p.strip() for p in partes if p.strip())
    if not corpo_extraido:
        avisos.append("Nenhum texto foi extraido de nenhum arquivo enviado.")

    titulo_sugerido = ""
    if nomes:
        titulo_sugerido = Path(nomes[0]).stem.replace("_", " ").replace("-", " ").strip().capitalize()

    id_ = _salvar_rascunho(
        {
            "titulo": titulo_sugerido,
            "fonte": "",
            "url": "",
            "categoria": "institucional",
            "corpo": corpo_extraido,
            "avisos": avisos,
            "arquivos": nomes,
        }
    )
    return RedirectResponse(f"/revisar/{id_}", status_code=303)


@app.get("/revisar/{id_}")
def revisar(id_: str) -> HTMLResponse:
    _validar_id(id_)
    rascunho = _carregar_rascunho(id_)
    if rascunho is None:
        raise HTTPException(404, "Rascunho nao encontrado (ja foi aprovado ou descartado?).")

    opcoes_categoria = "\n".join(
        f'<option value="{_esc(c)}"{" selected" if c == rascunho["categoria"] else ""}>{_esc(c)}</option>'
        for c in CATEGORIAS_DOC
    )
    opcoes_fonte = "\n".join(f'<option value="{_esc(f)}">' for f in _fontes_existentes())

    avisos_html = ""
    if rascunho.get("avisos"):
        itens = "".join(f"<li>{_esc(a)}</li>" for a in rascunho["avisos"])
        avisos_html = f'<div class="aviso"><strong>Avisos da extracao:</strong><ul>{itens}</ul></div>'

    corpo = f"""
<p>Arquivos enviados: {_esc(", ".join(rascunho["arquivos"]))}</p>
{avisos_html}
<form method="post">
  <label for="titulo">Titulo</label>
  <input type="text" id="titulo" name="titulo" value="{_esc(rascunho['titulo'])}" required>

  <label for="fonte">Fonte</label>
  <input type="text" id="fonte" name="fonte" value="{_esc(rascunho['fonte'])}" list="fontes" required>
  <datalist id="fontes">{opcoes_fonte}</datalist>

  <label for="url">URL (opcional)</label>
  <input type="url" id="url" name="url" value="{_esc(rascunho['url'])}">

  <label for="categoria">Categoria</label>
  <select id="categoria" name="categoria">{opcoes_categoria}</select>

  <label for="corpo">Conteudo (markdown) -- revise antes de aprovar</label>
  <textarea id="corpo" name="corpo">{_esc(rascunho['corpo'])}</textarea>

  <button type="submit" formaction="/aprovar/{id_}">Aprovar e salvar</button>
  <button type="submit" formaction="/descartar/{id_}" formnovalidate class="secundario">Descartar</button>
</form>
"""
    return _layout("Revisar", corpo)


@app.post("/aprovar/{id_}")
def aprovar(
    id_: str,
    titulo: str = Form(...),
    fonte: str = Form(...),
    url: str = Form(""),
    categoria: str = Form(...),
    corpo: str = Form(...),
) -> HTMLResponse:
    _validar_id(id_)
    if _carregar_rascunho(id_) is None:
        raise HTTPException(404, "Rascunho nao encontrado (ja foi aprovado ou descartado?).")

    titulo = titulo.strip()
    fonte = fonte.strip()
    corpo = corpo.strip()
    if not titulo or not fonte or not corpo:
        raise HTTPException(400, "Titulo, fonte e conteudo sao obrigatorios. Volte e preencha.")

    caminho_existente = _arquivo_existente_por_titulo_fonte(titulo, fonte)
    if caminho_existente is not None:
        caminho = caminho_existente
        acao = "atualizado"
    else:
        caminho = DIR_CONHECIMENTO / f"{_proximo_prefixo()}-{_slugificar(titulo)}.md"
        acao = "criado"

    post = frontmatter.Post(corpo, titulo=titulo, fonte=fonte, url=url.strip(), categoria=categoria)
    conteudo_final = frontmatter.dumps(post) + "\n"

    tmp = caminho.with_suffix(".md.tmp")
    tmp.write_text(conteudo_final, encoding="utf-8")
    tmp.replace(caminho)

    _apagar_rascunho(id_)

    corpo_html = f"""
<p>Documento <strong>{acao}</strong>: <code>{_esc(caminho.name)}</code></p>
<p>Para indexar na base, rode:</p>
<pre>docker compose run --rm bot python -m src.ingest</pre>
<p><a href="/">Importar outro arquivo</a></p>
"""
    return _layout("Aprovado", corpo_html)


@app.post("/descartar/{id_}")
def descartar(id_: str) -> RedirectResponse:
    _validar_id(id_)
    _apagar_rascunho(id_)
    return RedirectResponse("/", status_code=303)
