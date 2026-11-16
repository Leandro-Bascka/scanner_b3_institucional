# -*- coding: utf-8 -*-
"""
SCANNER B3 - INSTITUCIONAL QUANT (versão web / Streamlit)

Executa a varredura sob demanda (botão) e mostra tabela, leitura SMC
e gráfico do ativo escolhido. A lógica de análise vem de
scanner_b3_institucional.py (mesma usada na versão desktop).

Senha opcional: defina "senha" em Settings > Secrets do Streamlit Cloud.
"""

import hmac

import pandas as pd
import streamlit as st
from matplotlib.figure import Figure

import scanner_b3_institucional as sc


st.set_page_config(
    page_title="Scanner B3",
    page_icon="📈",
    layout="wide"
)


# ============================================================
# ACESSO (senha opcional)
# ============================================================

def autenticar():

    try:
        senha = st.secrets["senha"]
    except Exception:
        return True

    if st.session_state.get("autenticado"):
        return True

    digitada = st.text_input("Senha", type="password")

    if digitada:
        if hmac.compare_digest(str(digitada), str(senha)):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")

    return False


if not autenticar():
    st.stop()


# ============================================================
# CONSTANTES DE EXIBIÇÃO
# ============================================================

COLUNAS_COMPACTAS = [
    "PRECO", "ESTADO", "CLASSE", "DATA_1A", "IDADE",
    "SC_INST", "SC_REV", "SC_CONF", "VOL_REL", "RANK_FR",
    "ENTRADA", "STOP", "ALVO1", "ALVO2",
    "SR_POS", "SR_S1", "SR_S1F", "SR_R1", "SR_R1F", "SR_ESP",
    "VAL_CHECK",
    "SMC_CONF", "SMC_T1", "SMC_T2", "SMC_T3", "FALTA",
]

COR_ESTADO = {
    "REVERSÃO CONFIRMADA": "rgba(38, 166, 154, 0.45)",
    "CONTINUAÇÃO": "rgba(66, 165, 245, 0.40)",
    "REVERSÃO INICIAL": "rgba(253, 216, 53, 0.35)",
    "TENDÊNCIA EM ANDAMENTO": "rgba(102, 187, 106, 0.25)",
    "ESTICADA": "rgba(255, 138, 101, 0.40)",
}


# ============================================================
# BARRA LATERAL
# ============================================================

st.sidebar.title("Scanner B3")

score = int(
    st.sidebar.radio("Score mínimo", ["70", "85"], horizontal=True)
)

incluir_hoje = st.sidebar.checkbox(
    "Incluir candle de hoje (parcial)",
    value=True,
    help=(
        "Durante o pregão o candle do dia está incompleto: o volume "
        "relativo fica subestimado e alguns sinais só aparecem após o "
        "fechamento. Desmarque para analisar até o último fechamento."
    )
)

periodo = int(
    st.sidebar.selectbox("Gráfico (pregões)", ["60", "120", "250"], index=1)
)

tema = st.sidebar.radio("Tema do gráfico", ["escuro", "claro"], horizontal=True)

somente_sinais = st.sidebar.checkbox("Somente com sinal", value=True)
mostrar_smc = st.sidebar.checkbox("SMC no gráfico", value=True)
mostrar_sr = st.sidebar.checkbox("Suportes/resistências no gráfico", value=True)
todas_colunas = st.sidebar.checkbox("Mostrar todas as colunas", value=False)

varrer = st.sidebar.button(
    "VARRER A B3", type="primary", width="stretch"
)


# ============================================================
# VARREDURA
# ============================================================

if varrer:

    sc.DESCARTAR_BARRA_PARCIAL = not incluir_hoje

    barra = st.progress(0.0, text="Consultando liquidez e baixando dados...")

    def atualizar(i, total, ativo, estado):
        barra.progress(i / total, text=f"Analisando {ativo} ({i}/{total}) - {estado}")

    try:
        tabela, graficos, erros, tempo, smc, niveis = sc.executar_analise(
            score, callback=atualizar
        )

        st.session_state["resultado"] = {
            "tabela": tabela,
            "graficos": graficos,
            "erros": erros,
            "tempo": tempo,
            "smc": smc,
            "niveis": niveis,
            "hora": sc.agora_brasil(),
            "score": score,
            "incluiu_hoje": incluir_hoje,
        }

    except Exception as erro:
        st.error(f"Erro na varredura: {erro}")

    barra.empty()


# ============================================================
# RESULTADOS
# ============================================================

st.title("SCANNER B3 - INSTITUCIONAL QUANT")

resultado = st.session_state.get("resultado")

if resultado is None:
    st.info(
        "Clique em **VARRER A B3** na barra lateral. A varredura das "
        f"{sc.N_ATIVOS} ações mais líquidas leva alguns minutos."
    )
    st.stop()

tabela = resultado["tabela"]
graficos = resultado["graficos"]
smc = resultado["smc"]
niveis = resultado.get("niveis", {})

st.caption(
    f'Varredura de {resultado["hora"].strftime("%d/%m/%Y %H:%M")} (Brasília) '
    f'| score mínimo {resultado["score"]} '
    f'| candle de hoje {"incluído (parcial)" if resultado["incluiu_hoje"] else "excluído"} '
    f'| duração {str(resultado["tempo"]).split(".")[0]}'
)

contagem = tabela["ESTADO"].value_counts()

colunas_metricas = st.columns(6)

for coluna, (rotulo, estado) in zip(
    colunas_metricas,
    [
        ("Analisadas", None),
        ("Confirmadas", "REVERSÃO CONFIRMADA"),
        ("Continuações", "CONTINUAÇÃO"),
        ("Iniciais", "REVERSÃO INICIAL"),
        ("Tendência", "TENDÊNCIA EM ANDAMENTO"),
        ("Esticadas", "ESTICADA"),
    ]
):
    coluna.metric(
        rotulo,
        len(tabela) if estado is None else int(contagem.get(estado, 0))
    )

if resultado["erros"]:
    st.caption(f'Falhas de dados: {len(resultado["erros"])} ativo(s).')

visivel = (
    tabela[tabela["ESTADO"] != "SEM SINAL"]
    if somente_sinais else tabela
)

if visivel.empty:
    st.warning("Nenhum ativo com sinal nesta varredura.")
    st.stop()

colunas = sc.COLUNAS[1:] if todas_colunas else [
    c for c in COLUNAS_COMPACTAS if c in tabela.columns
]

exibicao = pd.DataFrame(index=visivel["ATIVO"])
exibicao.index.name = "ATIVO"

for coluna in colunas:
    exibicao[sc.TITULOS[coluna]] = [
        sc.formatar(v, sc.CASAS[coluna]) if coluna in sc.CASAS else v
        for v in visivel[coluna]
    ]

estados = dict(zip(visivel["ATIVO"], visivel["ESTADO"]))


def pintar(linha):
    cor = COR_ESTADO.get(estados.get(linha.name, ""), "")
    return [f"background-color: {cor}" if cor else ""] * len(linha)


st.dataframe(
    exibicao.style.apply(pintar, axis=1),
    width="stretch",
    height=min(520, 40 + 35 * len(exibicao)),
)


# ============================================================
# ATIVO SELECIONADO: LEITURA SMC + GRÁFICO
# ============================================================

ativo = st.selectbox("Ativo para o gráfico", visivel["ATIVO"].tolist())

linhas = tabela.set_index("ATIVO", drop=False).to_dict("index")
smc_ativo = smc.get(ativo)
sr_ativo = niveis.get(ativo)

if sr_ativo:
    st.subheader(f"Suportes e resistências | {ativo}")
    st.caption(sr_ativo["linhas"][0])
    for texto in sr_ativo["linhas"][1:-1]:
        recuo = "    " if texto.startswith("  ") else ""
        st.markdown(f"{recuo}- {texto.strip()}")
    st.caption(sr_ativo["linhas"][-1])

if smc_ativo:
    st.subheader(f"Leitura SMC | {ativo}")
    for texto in smc_ativo["linhas"]:
        st.markdown(f"- {texto}")
    st.caption(
        "Leitura em candles diários: níveis são zonas de reação prováveis, "
        "não garantias, e não constituem recomendação de investimento."
    )
else:
    st.caption(
        "Análise SMC disponível apenas para ações com REVERSÃO CONFIRMADA."
    )

figura = Figure(figsize=(14, 9), dpi=100)

sc.desenhar_grafico(
    figura,
    graficos[ativo],
    linhas[ativo],
    tema,
    periodo,
    smc_ativo if mostrar_smc else None,
    sr_ativo if mostrar_sr else None
)

st.pyplot(figura, clear_figure=True)
