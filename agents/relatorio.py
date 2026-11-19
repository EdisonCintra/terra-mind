"""G7: relatório de aplicação em PDF, gerado quando a OS é concluída (contrato 28).

Só usa dados do banco e do clima; nenhum LLM escreve nada aqui. O relatório traz o que a norma pede
para cada modalidade e, além disso, a conferência do clima contra os limites da bula, com a página:

- drone: Portaria MAPA 298/2021, art. 10 (registro de cada aplicação, guardado por 2 anos);
- avião: IN MAPA 2/2008, art. 9º (relatório operacional, assinado pelo responsável técnico);
- terrestre: não há modelo federal confirmado (D9 em aberto). O relatório serve de caderno de campo
  e de prova para auditoria, seguro e certificação. Os campos precisam da validação de um agrônomo.

O `sha256` é calculado sobre os dados (JSON canônico) e impresso no rodapé: quem recebe o PDF
confere com o registro guardado em `agente.relatorios_aplicacao`.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

import pymupdf

from agents.clientes.clima import ClimaIndisponivel
from agents.clientes.erp import ErpIndisponivel
from agents.deps import Dependencias
from agents.modelos import para_datetime
from agents.rules.conformidade import ItemConformidade, conferir_aplicacao, resumo_clima, veredito
from agents.rules.janelas import FUSO
from agents.rules.limites import limites_efetivos

logger = logging.getLogger(__name__)

BASE_LEGAL = {
    "drone": "Registro de aplicação com aeronave remotamente pilotada: Portaria MAPA 298/2021, art. 10.",
    "aerea": "Relatório operacional de aplicação aeroagrícola: IN MAPA 2/2008, art. 9º.",
    "terrestre": (
        "Aplicação terrestre: registro de campo. Não substitui o receituário agronômico nem documentos"
        " exigidos pelo órgão estadual."
    ),
}
MODALIDADES = {"drone": "Drone (ARP)", "aerea": "Aérea (avião agrícola)", "terrestre": "Terrestre"}
SITUACOES = {"conforme": "Conforme", "nao_conforme": "NÃO CONFORME", "sem_dado": "Sem dado"}
SITUACOES_REFERENCIA = {
    "conforme": "Dentro da referência",
    "nao_conforme": "Fora da referência",
    "sem_dado": "Sem dado",
}
MARGEM, LARGURA, ALTURA = 42, 595, 842
VERDE, VERMELHO, CINZA, PRETO = (0.1, 0.5, 0.25), (0.75, 0.15, 0.1), (0.4, 0.4, 0.4), (0, 0, 0)
LARANJA = (0.72, 0.42, 0.05)


class RelatorioIndisponivel(Exception):
    """A OS não existe, não tem execução registrada ou o ERP está fora do ar."""


def _data(valor: Any) -> str:
    if not valor:
        return "não informado"
    return f"{para_datetime(valor).astimezone(FUSO):%d/%m/%Y %H:%M}"


def _num(valor: Any, unidade: str = "", casas: int = 1) -> str:
    if valor is None:
        return "não informado"
    texto = f"{float(valor):.{casas}f}".replace(".", ",")
    return f"{texto} {unidade}".strip()


def _faixa(minimo: Any, maximo: Any, unidade: str) -> str:
    if minimo is None or maximo is None:
        return "não disponível"
    return f"{_num(minimo)} a {_num(maximo, unidade)}"


def montar_dados(
    os: dict, clima: dict | None, fonte_clima: str, itens: list[dict], conforme: bool | None
) -> dict:
    """Os dados do relatório, já no formato em que são guardados e resumidos no `sha256`."""
    e = os["execucao"]
    return {
        "os_id": os["id"],
        "modalidade": e.get("modalidade") or "terrestre",
        "talhao": {
            "nome": os.get("talhao"),
            "area_ha": os.get("area_ha"),
            "cultura": os.get("cultura"),
            "estagio": os.get("estagio_fenologico"),
            "lat": os.get("lat"),
            "lon": os.get("lon"),
        },
        "produto": {
            "nome": os.get("produto"),
            "tipo": os.get("tipo_insumo"),
            "unidade": os.get("unidade"),
            "bula": os.get("bula_id"),
            "dose_planejada_por_ha": os.get("dose_por_ha"),
        },
        "operacao": os.get("tipo_operacao"),
        "inicio_previsto": os.get("inicio_previsto"),
        "execucao": {
            chave: e.get(chave)
            for chave in (
                "inicio_real",
                "fim_real",
                "area_aplicada_ha",
                "dose_real_por_ha",
                "volume_calda_l_ha",
                "maquina",
                "operador",
                "receituario_numero",
                "responsavel_tecnico",
                "registro_profissional",
                "altura_voo_m",
                "largura_faixa_m",
                "aeronave",
                "ponta",
                "observacoes",
                "registrado_por",
            )
        },  # fmt: skip
        "clima": clima,
        "fonte_clima": fonte_clima,
        "conformidade": itens,
        "conforme": conforme,
    }


def resumo_sha256(dados: dict) -> str:
    canonico = json.dumps(dados, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


class _Pagina:
    """Escreve de cima para baixo e abre outra página quando a atual acaba."""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.pagina = doc.new_page(width=LARGURA, height=ALTURA)
        self.y = MARGEM

    def _espaco(self, altura: float) -> None:
        if self.y + altura > ALTURA - MARGEM - 30:
            self.pagina = self.doc.new_page(width=LARGURA, height=ALTURA)
            self.y = MARGEM

    def texto(
        self, texto: str, tamanho: float = 9.5, negrito: bool = False, cor=PRETO, x: float = MARGEM
    ) -> None:
        """Um parágrafo com quebra de linha automática."""
        fonte = "hebo" if negrito else "helv"
        linhas = _quebrar(texto, fonte, tamanho, LARGURA - MARGEM - x)
        self._espaco(len(linhas) * tamanho * 1.35)
        for linha in linhas:
            self.y += tamanho * 1.35
            self.pagina.insert_text((x, self.y), linha, fontsize=tamanho, fontname=fonte, color=cor)

    def secao(self, titulo: str) -> None:
        self._espaco(40)
        self.y += 12
        self.texto(titulo.upper(), tamanho=8.5, negrito=True, cor=CINZA)
        self.y += 3
        self.pagina.draw_line((MARGEM, self.y), (LARGURA - MARGEM, self.y), color=(0.8, 0.8, 0.8), width=0.6)

    def campos(self, pares: list[tuple[str, str]]) -> None:
        """Rótulo e valor em duas colunas."""
        meio = LARGURA / 2
        for i in range(0, len(pares), 2):
            self._espaco(26)
            self.y += 4
            topo = self.y
            for (rotulo, valor), x in zip(pares[i : i + 2], (MARGEM, meio), strict=False):
                self.pagina.insert_text((x, topo + 9), rotulo, fontsize=7.5, fontname="helv", color=CINZA)
                linhas = _quebrar(valor, "helv", 10, meio - MARGEM - 8)[:2]
                for n, linha in enumerate(linhas):
                    self.pagina.insert_text((x, topo + 21 + n * 12), linha, fontsize=10, fontname="helv")
                self.y = max(self.y, topo + 21 + (len(linhas) - 1) * 12)


def _quebrar(texto: str, fonte: str, tamanho: float, largura: float) -> list[str]:
    linhas: list[str] = []
    for paragrafo in str(texto).split("\n"):
        atual = ""
        for palavra in paragrafo.split():
            tentativa = f"{atual} {palavra}".strip()
            if atual and pymupdf.get_text_length(tentativa, fontname=fonte, fontsize=tamanho) > largura:
                linhas.append(atual)
                atual = palavra
            else:
                atual = tentativa
        linhas.append(atual)
    return linhas


def gerar_pdf(dados: dict, sha256: str, versao: int, gerado_em: datetime) -> bytes:
    doc = pymupdf.open()
    p = _Pagina(doc)
    e, talhao, produto, clima = dados["execucao"], dados["talhao"], dados["produto"], dados["clima"]
    modalidade = dados["modalidade"]

    p.texto("Relatório de aplicação de defensivo agrícola", tamanho=16, negrito=True)
    p.texto(f"OS {dados['os_id']} · {MODALIDADES.get(modalidade, modalidade)} · versão {versao}", cor=CINZA)
    p.texto(BASE_LEGAL.get(modalidade, ""), tamanho=8, cor=CINZA)

    conforme = dados["conforme"]
    if conforme is True:
        p.texto(
            "Aplicação dentro dos limites da bula conferidos abaixo.", tamanho=11, negrito=True, cor=VERDE
        )
    elif conforme is False:
        p.texto("Aplicação FORA de um limite da bula. Veja a conferência abaixo.", 11, True, VERMELHO)
    else:
        p.texto("Conformidade com a bula não conferida (bula sem limites lidos ou clima sem dado).", 11, True)

    p.secao("Área tratada")
    coordenadas = "não informado"
    if talhao.get("lat") is not None and talhao.get("lon") is not None:
        coordenadas = f"{float(talhao['lat']):.6f}, {float(talhao['lon']):.6f}"
    p.campos(
        [
            ("Talhão", str(talhao.get("nome") or "não informado")),
            ("Coordenadas geográficas (WGS84)", coordenadas),
            (
                "Cultura e estágio",
                f"{talhao.get('cultura') or 'não informado'} · {talhao.get('estagio') or '-'}",
            ),
            ("Área tratada", _num(e.get("area_aplicada_ha"), "ha", 2)),
        ]
    )

    p.secao("Produto e dose")
    unidade = produto.get("unidade") or ""
    p.campos(
        [
            ("Marca comercial", str(produto.get("nome") or "não informado")),
            ("Classe", str(produto.get("tipo") or "não informado")),
            ("Dose aplicada", _num(e.get("dose_real_por_ha"), f"{unidade}/ha", 3)),
            ("Dose planejada na OS", _num(produto.get("dose_planejada_por_ha"), f"{unidade}/ha", 3)),
            ("Volume de calda", _num(e.get("volume_calda_l_ha"), "L/ha")),
            ("Receituário agronômico", str(e.get("receituario_numero") or "não informado")),
        ]
    )

    p.secao("Execução")
    campos = [
        ("Início", _data(e.get("inicio_real"))),
        ("Término", _data(e.get("fim_real"))),
        ("Máquina ou aeronave", str(e.get("aeronave") or e.get("maquina") or "não informado")),
        ("Operador", str(e.get("operador") or "não informado")),
        ("Responsável técnico", str(e.get("responsavel_tecnico") or "não informado")),
        ("Registro profissional", str(e.get("registro_profissional") or "não informado")),
    ]
    if modalidade != "terrestre":
        campos += [
            ("Altura do voo", _num(e.get("altura_voo_m"), "m")),
            ("Largura da faixa", _num(e.get("largura_faixa_m"), "m")),
        ]
    campos.append(("Ponta de pulverização", str(e.get("ponta") or "não informado")))
    p.campos(campos)
    if e.get("observacoes"):
        p.y += 4
        p.texto(f"Observações: {e['observacoes']}")

    p.secao("Clima durante a aplicação")
    if clima is None:
        p.texto("Sem dado de clima para o intervalo da aplicação.")
    else:
        direcao = f", de {clima['vento_direcao']}" if clima.get("vento_direcao") else ""
        inicial, final = clima["inicial"], clima["final"]
        p.campos(
            [
                ("Temperatura", _faixa(clima["temp_min_c"], clima["temp_max_c"], "°C")),
                ("Umidade relativa", _faixa(clima["umidade_min_pct"], clima["umidade_max_pct"], "%")),
                ("Vento", _faixa(clima["vento_min_kmh"], clima["vento_max_kmh"], "km/h") + direcao),
                ("Rajada máxima", _num(clima.get("rajada_max_kmh"), "km/h")),
                ("Delta T", _faixa(clima.get("delta_t_min_c"), clima.get("delta_t_max_c"), "°C")),
                ("Chuva no período", _num(clima.get("chuva_mm"), "mm")),
                (
                    "No início",
                    f"{_num(inicial['temp_c'], '°C')} · {_num(inicial['umidade_pct'], '%', 0)}"
                    f" · {_num(inicial['vento_kmh'], 'km/h')}",
                ),
                (
                    "No término",
                    f"{_num(final['temp_c'], '°C')} · {_num(final['umidade_pct'], '%', 0)}"
                    f" · {_num(final['vento_kmh'], 'km/h')}",
                ),
            ]
        )
    p.y += 4
    p.texto(f"Fonte do clima: {dados['fonte_clima']}.", tamanho=8, cor=CINZA)

    p.secao("Conferência com os limites")
    if not dados["conformidade"]:
        p.texto("Nenhum limite para conferir.")
    for item in dados["conformidade"]:
        # "Conforme" e "NÃO CONFORME" só para limite da bula; a referência genérica não é norma.
        rotulos = SITUACOES if item["da_bula"] else SITUACOES_REFERENCIA
        cores = (
            {"conforme": VERDE, "nao_conforme": VERMELHO} if item["da_bula"] else {"nao_conforme": LARANJA}
        )
        previsto = " (previsão: o período do limite ainda não terminou)" if item.get("previsto") else ""
        p.texto(
            f"{rotulos[item['situacao']]} · {item['regra']} · {item['fonte']}{previsto}",
            negrito=item["da_bula"],
            cor=cores.get(item["situacao"], CINZA if item["situacao"] == "sem_dado" else PRETO),
        )
        if item.get("detalhe"):
            p.texto(item["detalhe"], tamanho=8.5, cor=CINZA, x=MARGEM + 10)

    p.secao("Assinaturas")
    p.y += 30
    for x, rotulo in ((MARGEM, "Responsável técnico"), (LARGURA / 2 + 10, "Operador ou executor")):
        p.pagina.draw_line((x, p.y), (x + 220, p.y), color=PRETO, width=0.6)
        p.pagina.insert_text((x, p.y + 11), rotulo, fontsize=8, fontname="helv", color=CINZA)

    rodape = (
        f"Gerado pelo TerraMind em {gerado_em.astimezone(FUSO):%d/%m/%Y %H:%M} a partir dos dados da OS,"
        f" sem edição manual. SHA-256 dos dados: {sha256}"
    )
    for numero, pagina in enumerate(doc, start=1):
        for n, linha in enumerate(_quebrar(rodape, "helv", 6.5, LARGURA - 2 * MARGEM - 40)):
            pagina.insert_text(
                (MARGEM, ALTURA - 30 + n * 8), linha, fontsize=6.5, fontname="helv", color=CINZA
            )
        pagina.insert_text(
            (LARGURA - MARGEM - 30, ALTURA - 30), f"{numero}/{len(doc)}", fontsize=7, color=CINZA
        )
    conteudo = doc.tobytes(deflate=True)
    doc.close()
    return conteudo


class ServicoRelatorio:
    def __init__(self, deps: Dependencias):
        self.deps = deps

    async def _clima(self, os: dict, inicio: datetime, fim: datetime, limites, agora: datetime):
        """(resumo, fonte, itens de conformidade). Sem clima, o relatório sai mesmo assim, dizendo isso."""
        if self.deps.clima is None or os.get("lat") is None or os.get("lon") is None:
            return None, "clima não disponível", []  # sem itens: o veredito fica "não conferido"
        passados = (agora.astimezone(FUSO).date() - inicio.astimezone(FUSO).date()).days + 1
        try:
            previsao = await self.deps.clima.previsao(
                float(os["lat"]), float(os["lon"]), dias=3, dias_passados=max(passados, 1)
            )
        except ClimaIndisponivel as e:
            logger.warning("relatório da OS %s sem clima: %s", os.get("id"), e)
            return None, "clima não disponível no momento da geração", []
        resumo = resumo_clima(previsao.horas, inicio, fim)
        if resumo is None:
            return None, f"{previsao.fonte}; sem dado para o intervalo da aplicação", []
        itens = conferir_aplicacao(previsao.horas, limites, inicio, fim, agora)
        return resumo, previsao.fonte, itens

    async def gerar(self, os_id: int) -> dict:
        """Gera e guarda uma nova versão do relatório. Devolve `{id, os_id, versao, gerado_em, sha256,
        conforme}`."""
        try:
            os = await self.deps.erp.detalhar_execucao(os_id)
        except ErpIndisponivel as e:
            raise RelatorioIndisponivel(str(e)) from e
        execucao = os["execucao"]
        inicio, fim = para_datetime(execucao["inicio_real"]), para_datetime(execucao["fim_real"])
        agora = self.deps.relogio()
        limites = (
            limites_efetivos(await self.deps.repo.limites_do_produto(os["produto"]))
            if os.get("produto")
            else []
        )
        itens: list[ItemConformidade]
        clima, fonte, itens = await self._clima(os, inicio, fim, limites, agora)
        # Só os limites da bula decidem o veredito; os genéricos aparecem como referência.
        conforme = veredito(itens)
        dados = montar_dados(os, clima, fonte, [i.model_dump(mode="json") for i in itens], conforme)
        sha = resumo_sha256(dados)
        versao = await self.deps.repo.proxima_versao_relatorio(os_id)
        pdf = gerar_pdf(dados, sha, versao, agora)
        registro = await self.deps.repo.salvar_relatorio(os_id, versao, dados, sha, pdf, conforme)
        await self.deps.repo.auditar(
            None,
            "relatorio",
            "gerar_relatorio_aplicacao",
            depois={"os_id": os_id, "relatorio_id": registro["id"], "sha256": sha, "conforme": conforme},
        )
        return {**registro, "sha256": sha, "conforme": conforme}
