# -*- coding: utf-8 -*-
"""
Gera o site estático do scanner (HTML) a partir da varredura da B3.

Uso:  python gerar_site.py [pasta_de_saida]      (padrão: site)

Cria duas páginas: index.html (score mínimo 70) e score85.html.
Cada página traz a tabela ordenável, os suportes/resistências, a leitura
SMC e o gráfico de cada ativo com sinal. É executado de hora em hora pelo GitHub Actions.
"""

import base64
import html
import io
import sys
import time
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

import scanner_b3_institucional as sc


SCORES = (70, 85)
PAGINAS = {70: "index.html", 85: "score85.html"}
MAX_GRAFICOS = 40
PERIODO_GRAFICO = 120

COLUNAS_SITE = [
    "ATIVO", "PRECO", "ESTADO", "CLASSE", "DATA_1A", "IDADE",
    "SC_INST", "SC_REV", "SC_CONF", "VOL_REL", "RANK_FR",
    "ENTRADA", "STOP", "ALVO1", "ALVO2", "RISCO",
    "SR_POS", "SR_S1", "SR_S1F", "SR_R1", "SR_R1F", "SR_ESP", "SR_RR",
    "SMC_CONF", "SMC_ESTRUT", "SMC_ZONA",
    "SMC_T1", "SMC_T2", "SMC_T3", "SMC_UP", "SMC_STOP", "SMC_RR",
    "FALTA",
]

# colunas de texto (alinhadas à esquerda)
COLUNAS_TEXTO = (
    "ESTADO", "CLASSE", "FALTA", "SR_POS", "SMC_ESTRUT", "SMC_ZONA"
)

ESTADOS = [
    "REVERSÃO CONFIRMADA", "CONTINUAÇÃO", "REVERSÃO INICIAL",
    "TENDÊNCIA EM ANDAMENTO", "ESTICADA",
]


# ============================================================
# ROBUSTEZ DE REDE
# ============================================================

def com_tentativas(funcao, tentativas=3, espera=45):

    def interna(*args, **kwargs):

        ultimo = None

        for tentativa in range(1, tentativas + 1):
            try:
                return funcao(*args, **kwargs)
            except Exception as erro:
                ultimo = erro
                print(f"Tentativa {tentativa}/{tentativas} falhou: {erro}")
                if tentativa < tentativas:
                    time.sleep(espera)

        raise ultimo

    return interna


def preparar_coleta():
    """Baixa os dados uma só vez e reaproveita nas duas varreduras."""

    obter = com_tentativas(sc.obter_acoes_mais_liquidas)
    baixar = com_tentativas(sc.baixar_dados)

    cache = {}

    def obter_memo():
        if "ativos" not in cache:
            cache["ativos"] = obter()
        return cache["ativos"]

    def baixar_memo(ativos):
        if "dados" not in cache:
            cache["dados"] = baixar(ativos)
        return cache["dados"]

    sc.obter_acoes_mais_liquidas = obter_memo
    sc.baixar_dados = baixar_memo


# ============================================================
# GRÁFICOS
# ============================================================

def gerar_imagem(graficos, linhas, smc, niveis, ativo):

    figura = Figure(figsize=(14, 9), dpi=72)

    sc.desenhar_grafico(
        figura, graficos[ativo], linhas[ativo], "escuro",
        PERIODO_GRAFICO, smc.get(ativo), niveis.get(ativo)
    )

    memoria = io.BytesIO()
    figura.savefig(
        memoria, format="png", facecolor=figura.get_facecolor()
    )

    return base64.b64encode(memoria.getvalue()).decode("ascii")


# ============================================================
# HTML
# ============================================================

CSS = """
:root{--bg:#0e1116;--painel:#161b22;--cab:#21262d;--fg:#e6edf3;--suave:#8b949e;
--grade:#30363d;--link:#58a6ff;--sel:#1f4e79;
--conf:rgba(38,166,154,.40);--cont:rgba(66,165,245,.35);--ini:rgba(253,216,53,.28);
--tend:rgba(102,187,106,.22);--esti:rgba(255,138,101,.38)}
body.claro{--bg:#f5f6f8;--painel:#fff;--cab:#e4e7eb;--fg:#1f2933;--suave:#616e7c;
--grade:#d0d5dc;--link:#0969da;--sel:#bbdefb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
header,main,footer{padding:12px 16px;max-width:1700px;margin:0 auto}
h1{font-size:20px;margin:6px 0}
.meta{color:var(--suave);font-size:13px}
nav a{color:var(--link);margin-right:14px;text-decoration:none;font-weight:600}
nav a.atual{color:var(--fg);border-bottom:2px solid var(--link)}
.barra{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:10px 0}
select,button{background:var(--painel);color:var(--fg);border:1px solid var(--grade);
border-radius:6px;padding:6px 10px;font-size:13px}
button{cursor:pointer}
.resumo{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.chip{background:var(--painel);border:1px solid var(--grade);border-radius:8px;padding:6px 12px}
.chip b{font-size:16px;margin-left:6px}
.rolagem{overflow-x:auto;border:1px solid var(--grade);border-radius:8px;background:var(--painel)}
table{border-collapse:collapse;width:100%;white-space:nowrap}
th{background:var(--cab);position:sticky;top:0;cursor:pointer;text-align:center;
padding:8px 10px;font-size:12px;border-bottom:1px solid var(--grade)}
td{padding:6px 10px;text-align:center;border-bottom:1px solid var(--grade)}
td.l,th.l{text-align:left}
tbody tr{cursor:pointer}
tbody tr:hover{outline:1px solid var(--link)}
tr.sel{outline:2px solid var(--link)}
tr.e-confirmado td{background:var(--conf)}
tr.e-continuacao td{background:var(--cont)}
tr.e-inicial td{background:var(--ini)}
tr.e-tendencia td{background:var(--tend)}
tr.e-esticada td{background:var(--esti)}
#detalhe{margin-top:16px;background:var(--painel);border:1px solid var(--grade);
border-radius:8px;padding:12px 16px;scroll-margin-top:8px}
#detalhe img{max-width:100%;height:auto;border-radius:6px;margin-top:8px}
#detalhe li{margin:4px 0}
footer{color:var(--suave);font-size:12px}
"""

JS = """
(function(){
  var tabela = document.getElementById('tabela');
  var corpo = tabela.tBodies[0];
  var linhas = Array.prototype.slice.call(corpo.rows);
  var selEstado = document.getElementById('f-estado');
  var chkTodos = document.getElementById('f-todos');
  var numero = /^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$/;

  function filtrar(){
    var est = selEstado.value, todos = chkTodos.checked, n = 0;
    linhas.forEach(function(tr){
      var ok = (todos || tr.dataset.sinal === '1') && (!est || tr.dataset.estado === est);
      tr.style.display = ok ? '' : 'none';
      if (ok) n++;
    });
    document.getElementById('contagem').textContent = n + ' ativo(s) na tabela';
  }
  selEstado.addEventListener('change', filtrar);
  chkTodos.addEventListener('change', filtrar);

  var ordem = {};
  Array.prototype.forEach.call(tabela.tHead.rows[0].cells, function(th, idx){
    th.addEventListener('click', function(){
      var asc = !ordem[idx]; ordem = {}; ordem[idx] = asc;
      linhas.sort(function(a, b){
        var va = a.cells[idx].dataset.v || '', vb = b.cells[idx].dataset.v || '';
        if (va === '' && vb === '') return 0;
        if (va === '') return 1;
        if (vb === '') return -1;
        var r = (numero.test(va) && numero.test(vb))
          ? parseFloat(va) - parseFloat(vb)
          : va.localeCompare(vb, 'pt-BR');
        return asc ? r : -r;
      });
      linhas.forEach(function(tr){ corpo.appendChild(tr); });
    });
  });

  function mostrar(tr){
    var alvo = document.getElementById('detalhe');
    var t = document.getElementById('t-' + tr.dataset.ativo);
    alvo.innerHTML = '';
    if (t) { alvo.appendChild(t.content.cloneNode(true)); }
    else { alvo.textContent = 'Sem detalhes para ' + tr.dataset.ativo + '.'; }
    linhas.forEach(function(x){ x.classList.remove('sel'); });
    tr.classList.add('sel');
    alvo.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  linhas.forEach(function(tr){ tr.addEventListener('click', function(){ mostrar(tr); }); });

  var botao = document.getElementById('tema');
  try { if (localStorage.getItem('tema') === 'claro') document.body.classList.add('claro'); } catch(e) {}
  botao.addEventListener('click', function(){
    document.body.classList.toggle('claro');
    try { localStorage.setItem('tema', document.body.classList.contains('claro') ? 'claro' : 'escuro'); } catch(e) {}
  });

  filtrar();
})();
"""


def valor_ordenacao(coluna, valor):

    if coluna == "DATA_1A":
        try:
            dia, mes, ano = str(valor).split("/")
            return f"{ano}{mes}{dia}"
        except ValueError:
            return ""

    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "" if valor in ("-", None) else str(valor)

    return repr(numero) if np.isfinite(numero) else ""


def montar_pagina(score, resultado, imagens, meta):

    tabela, graficos, erros, tempo, smc, niveis = resultado

    esc = html.escape

    contagem = tabela["ESTADO"].value_counts()

    chips = "".join(
        f'<div class="chip">{esc(rotulo)}<b>{valor}</b></div>'
        for rotulo, valor in [
            ("Analisadas", len(tabela)),
            ("Confirmadas", int(contagem.get("REVERSÃO CONFIRMADA", 0))),
            ("Continuações", int(contagem.get("CONTINUAÇÃO", 0))),
            ("Iniciais", int(contagem.get("REVERSÃO INICIAL", 0))),
            ("Tendência", int(contagem.get("TENDÊNCIA EM ANDAMENTO", 0))),
            ("Esticadas", int(contagem.get("ESTICADA", 0))),
        ]
    )

    cabecalho = "".join(
        f'<th class="{"l" if c in COLUNAS_TEXTO else ""}">'
        f'{esc(sc.TITULOS[c])}</th>'
        for c in COLUNAS_SITE
    )

    linhas_html = []
    modelos = []

    for _, linha in tabela.iterrows():

        ativo = linha["ATIVO"]
        estado = linha["ESTADO"]
        classe = sc.TAG_ESTADO.get(estado, "")
        tem_sinal = "0" if estado == "SEM SINAL" else "1"

        celulas = []

        for coluna in COLUNAS_SITE:

            bruto = linha[coluna]

            texto = (
                sc.formatar(bruto, sc.CASAS[coluna])
                if coluna in sc.CASAS else str(bruto)
            )

            esq = ' class="l"' if coluna in COLUNAS_TEXTO else ""

            celulas.append(
                f'<td{esq} data-v="{esc(valor_ordenacao(coluna, bruto))}">'
                f'{"<b>" + esc(texto) + "</b>" if coluna == "ATIVO" else esc(texto)}</td>'
            )

        linhas_html.append(
            f'<tr class="e-{classe}" data-ativo="{esc(ativo)}" '
            f'data-estado="{esc(estado)}" data-sinal="{tem_sinal}">'
            + "".join(celulas) + "</tr>"
        )

        if ativo in imagens or ativo in smc or ativo in niveis:

            partes = [f"<h3>{esc(ativo)} — {esc(estado)}</h3>"]

            if ativo in niveis:
                sr_linhas = niveis[ativo]["linhas"]
                partes.append(
                    "<h4>Suportes e resistências</h4>"
                    f'<p class="meta">{esc(sr_linhas[0])}</p><ul>'
                    + "".join(
                        f'<li style="margin-left:{18 if t.startswith("  ") else 0}px;'
                        f'list-style:{"circle" if t.startswith("  ") else "disc"}">'
                        f"{esc(t.strip())}</li>"
                        for t in sr_linhas[1:-1]
                    )
                    + f'</ul><p class="meta">{esc(sr_linhas[-1])}</p>'
                )

            if ativo in smc:
                partes.append("<h4>Leitura SMC</h4>")

            if ativo in smc:
                partes.append(
                    "<ul>"
                    + "".join(f"<li>{esc(t)}</li>" for t in smc[ativo]["linhas"])
                    + "</ul>"
                )
            elif estado == "SEM SINAL":
                partes.append("<p>Sem sinal nesta varredura.</p>")
            else:
                partes.append(
                    "<p>Análise SMC disponível apenas para ações com "
                    "REVERSÃO CONFIRMADA.</p>"
                )

            if linha["FALTA"] not in ("-", None):
                partes.append(
                    f'<p><b>Falta para confirmar reversão:</b> {esc(str(linha["FALTA"]))}</p>'
                )

            if ativo in imagens:
                partes.append(
                    f'<img alt="Gráfico {esc(ativo)}" '
                    f'src="data:image/png;base64,{imagens[ativo]}">'
                )

            modelos.append(
                f'<template id="t-{esc(ativo)}">{"".join(partes)}</template>'
            )

    opcoes = "".join(
        f'<option value="{esc(e)}">{esc(e)}</option>' for e in ESTADOS
    )

    nav = " ".join(
        f'<a href="{arq}"{" class=atual" if s == score else ""}>Score ≥ {s}</a>'
        for s, arq in PAGINAS.items()
    )

    aviso_parcial = (
        " — <b>pregão em andamento: candle de hoje parcial, "
        "volume relativo subestimado</b>"
        if meta["parcial"] else ""
    )

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner B3 — score ≥ {score}</title>
<style>{CSS}</style>
</head>
<body>
<header>
<h1>SCANNER B3 — INSTITUCIONAL QUANT</h1>
<nav>{nav}</nav>
<div class="meta">Atualizado em {esc(meta["hora"])} (Brasília){aviso_parcial}.
Último candle: {esc(meta["ultimo_candle"])} · {len(tabela)} ações · duração da varredura {esc(str(tempo).split(".")[0])}.</div>
<div class="resumo">{chips}</div>
<div class="barra">
<label>Estado: <select id="f-estado"><option value="">Todos</option>{opcoes}</select></label>
<label><input type="checkbox" id="f-todos"> Mostrar ativos sem sinal</label>
<button id="tema" type="button">Alternar tema</button>
<span class="meta" id="contagem"></span>
</div>
</header>
<main>
<div class="rolagem">
<table id="tabela">
<thead><tr>{cabecalho}</tr></thead>
<tbody>
{"".join(linhas_html)}
</tbody>
</table>
</div>
<div id="detalhe"><span class="meta">Toque numa linha para ver a leitura SMC e o gráfico.</span></div>
{"".join(modelos)}
</main>
<footer>
Leitura em candles diários (fonte: Yahoo Finance, universo: Fundamentus). Níveis e alvos são zonas de
reação prováveis, não garantias, e nada aqui é recomendação de investimento. Dados podem ter atraso.
</footer>
<script>{JS}</script>
</body>
</html>
"""


# ============================================================
# PRINCIPAL
# ============================================================

def main(saida="site"):

    sc.DESCARTAR_BARRA_PARCIAL = False   # inclui o candle de hoje
    preparar_coleta()

    pasta = Path(saida)
    pasta.mkdir(parents=True, exist_ok=True)

    for score in SCORES:

        print(f"\n=== Varredura com score mínimo {score} ===")

        resultado = sc.executar_analise(
            score, callback=lambda *args: None
        )

        tabela, graficos, erros, tempo, smc, niveis = resultado

        linhas = tabela.set_index("ATIVO", drop=False).to_dict("index")

        imagens = {}

        com_sinal = tabela[tabela["ESTADO"] != "SEM SINAL"]["ATIVO"].tolist()

        for ativo in com_sinal[:MAX_GRAFICOS]:
            imagens[ativo] = gerar_imagem(graficos, linhas, smc, niveis, ativo)

        agora = sc.agora_brasil()

        ultimo = max(df.index[-1] for df in graficos.values())

        meta = {
            "hora": agora.strftime("%d/%m/%Y %H:%M"),
            "ultimo_candle": ultimo.strftime("%d/%m/%Y"),
            "parcial": (
                agora.weekday() < 5
                and agora.hour < 18
                and ultimo.date() == agora.date()
            ),
        }

        pagina = montar_pagina(score, resultado, imagens, meta)

        caminho = pasta / PAGINAS[score]
        caminho.write_text(pagina, encoding="utf-8")

        print(
            f"{caminho}: {len(tabela)} ações, "
            f"{len(com_sinal)} com sinal, {len(imagens)} gráficos, "
            f"{caminho.stat().st_size / 1e6:.1f} MB"
        )

    (pasta / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":

    main(sys.argv[1] if len(sys.argv) > 1 else "site")
