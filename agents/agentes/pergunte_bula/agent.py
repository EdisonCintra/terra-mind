"""Pergunte à bula (G4): responde a pergunta do gerente ou do operador só com trechos das bulas.

Roda fora do workflow do incidente, por `agents.bula.PergunteBula`. A ferramenta guarda em
`state["trechos_vistos"]` tudo o que a busca devolveu; o código confere depois que cada citação veio
dali (`agents.rules.citacao`). O LLM nunca é a fonte do número: ele só aponta o trecho.
"""

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext

from agents.agentes._comum import criar_agente
from agents.deps import Dependencias
from agents.modelos import RespostaBula
from agents.rules.citacao import normalizar

CHAVE_VISTOS = "trechos_vistos"
CHAVE_RESPOSTA = "resposta_bula"
LIMITE_TRECHOS = 6  # a busca do conhecimento erra o top 3 em algumas bulas (carência, estresse hídrico)
LIMITE_BUSCA_AMPLA = 10  # máximo que o conhecimento aceita (rotas_trechos: le=10)
SAIDA_VAZIA = '{"encontrou": false, "resposta": "", "citacoes": []}'
CAMPOS = ("texto", "fonte_doc", "pagina", "secao", "produto")


async def _produto_aproximado(deps: Dependencias, consulta: str, produto: str) -> list[dict]:
    """O filtro do conhecimento exige o nome exato. No celular a pessoa escreve "abacus" ou "BRUCIA":
    busca sem filtro e fica só com os trechos de um produto cujo nome contém o que foi escrito."""
    alvo = normalizar(produto)
    trechos = await deps.conhecimento.buscar_trechos(f"{produto} {consulta}", limite=LIMITE_BUSCA_AMPLA)
    proximos = [t for t in trechos if t.get("produto") and alvo in normalizar(t["produto"])]
    return proximos[:LIMITE_TRECHOS]


def criar_buscar_na_bula(deps: Dependencias):
    async def buscar_na_bula(consulta: str, tool_context: ToolContext, produto: str | None = None) -> dict:
        """Busca trechos das bulas dos defensivos, com fonte e página para citar.

        Args:
            consulta: o que procurar, em português, com as palavras que a bula usaria (ex.: "velocidade
                do vento", "intervalo de segurança", "intervalo de reentrada", "número máximo de aplicações").
            produto: nome comercial exato do produto (ex.: "Abacus HC"). Informe sempre que a pergunta
                citar um produto.

        Returns:
            {"ok": true, "trechos": [{"texto", "fonte_doc", "pagina", "secao", "produto"}]}. Lista vazia
            significa que a bula não traz o assunto com essas palavras: tente outras ou responda que
            não encontrou.
        """
        trechos = await deps.conhecimento.buscar_trechos(consulta, produto=produto, limite=LIMITE_TRECHOS)
        if produto and not trechos:
            trechos = await _produto_aproximado(deps, consulta, produto)
        achados = [{k: t.get(k) for k in CAMPOS} for t in trechos]
        tool_context.state[CHAVE_VISTOS] = [*(tool_context.state.get(CHAVE_VISTOS) or []), *achados]
        return {"ok": True, "trechos": achados}

    return buscar_na_bula


def instrucao(rc: ReadonlyContext) -> str:
    estado = rc.state
    produto = estado.get("produto")
    sobre = (
        f'O produto escolhido é "{produto}".'
        if produto
        else "Nenhum produto foi escolhido: se a pergunta citar um produto, passe o nome dele em `produto`"
        ' como escrito (ex.: "abacus"). Se não citar, busque sem `produto` e diga na resposta de qual'
        " produto é o trecho."
    )
    return f"""Você é o "Pergunte à bula" do TerraMind. Responda a pergunta abaixo SÓ com o que os
trechos das bulas devolvidos por buscar_na_bula dizem. Responda em português.

{sobre}

Como trabalhar:
1. Chame buscar_na_bula com o produto e uma consulta com as palavras que a bula usaria. Se os trechos
   não responderem, tente no máximo mais duas consultas diferentes. Consultas que funcionam:
   - carência ou intervalo de segurança: exatamente "INTERVALO DE SEGURANÇA";
   - vento, umidade e temperatura: "condições meteorológicas" ou "condições climáticas";
   - entrada de pessoas na área: "intervalo de reentrada".
2. As tabelas da bula chegam desmontadas, um valor por linha, na ordem das colunas. Ex.: "INTERVALO DE
   SEGURANÇA: Cultura Dias Milho 45 Soja 14" quer dizer milho 45 dias e soja 14 dias; o número logo
   depois do nome da cultura é o dela. Leia a tabela assim e responda.
3. Se algum trecho responder, encontrou=true. Em `resposta`, até 3 frases, com os números exatamente
   como estão no trecho (unidades incluídas). Se a pergunta for "posso aplicar...?", diga sim ou não e por
   quê, pelo limite do trecho.
4. Em `citacoes`, para cada trecho usado: `trecho` com o texto LITERAL copiado do trecho (pode cortar
   com "..." no meio, mas não reescreva), e `fonte_doc` e `pagina` exatamente como vieram da ferramenta.
5. Se nenhum trecho responder, encontrou=false, resposta "A bula não traz isso." e citacoes vazia.
   Nunca responda de memória, nunca invente número, preço, dose ou página.

Pergunta: {estado.get("pergunta", "")}"""


def criar(deps: Dependencias) -> Agent:
    return criar_agente(
        deps,
        "bula",
        name="pergunte_bula",
        description="Responde perguntas sobre as bulas citando trecho, fonte e página.",
        instruction=instrucao,
        tools=[criar_buscar_na_bula(deps)],
        output_schema=RespostaBula,
        output_key=CHAVE_RESPOSTA,
        saida_vazia=SAIDA_VAZIA,
    )


def __getattr__(nome: str):
    if nome == "root_agent":
        from agents.agentes._demo import _cenario_dos_agentes

        agente = criar(_cenario_dos_agentes().deps)

        async def pergunta_da_conversa(callback_context):
            # No `adk web`, a pergunta é a mensagem digitada.
            conteudo = callback_context.user_content
            texto = "".join(p.text or "" for p in (conteudo.parts if conteudo else []))
            callback_context.state["pergunta"] = texto
            callback_context.state[CHAVE_VISTOS] = []

        agente.before_agent_callback = pergunta_da_conversa
        return agente
    raise AttributeError(nome)
