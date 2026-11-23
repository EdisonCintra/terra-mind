# Como criar uma tela no painel

O painel é FastAPI + Jinja2 + HTMX. Toda tela nova segue os cinco passos abaixo.
Veja os componentes prontos em `/componentes` (vitrine) e em `painel/templates/componentes.html`.

1. **Rota em `painel/main.py`**, juntando `contexto_base(request)` ao contexto do template:

   ```python
   @app.get("/exemplo", response_class=HTMLResponse)
   def tela_exemplo(request: Request):
       contexto = contexto_base(request, ativa="/exemplo")
       contexto["linhas"] = dados.listar_exemplo()
       return templates.TemplateResponse(request, "exemplo.html", contexto)
   ```

2. **Template em `painel/templates/`** estendendo `layout.html` e usando os componentes:

   ```jinja
   {% extends "layout.html" %}
   {% import "componentes.html" as c %}
   {% block titulo %}Exemplo · TerraMind{% endblock %}
   {% block conteudo %}
     <div class="pagina-interna">
       <header class="cabecalho-pagina"><h1>Exemplo</h1><p class="nota">Uma linha sobre a tela.</p></header>
       <section class="kpis kpis-3">{{ c.kpi("Total", total, "nota", id="kpi-total") }}</section>
       <section class="painel">{{ c.tabela(["Nome", "Status"], linhas, "Nada por aqui") }}</section>
     </div>
   {% endblock %}
   ```

   Blocos do layout: `titulo`, `conteudo`, `acoes_topo` (botões no topo) e `scripts` (JS só da tela, ex.: `agenda.js`).
   Contêiner: todo conteúdo vai em `<div class="pagina-interna">` (mesma largura e margem do topo, 1520 px;
   blocos empilhados com 1 rem de espaço). Título da tela em `<header class="cabecalho-pagina">` com `<h1>`.
   Cor só para estado: use tom `neutro` quando não há dado; o tom vem do dado (ex.: acima do limiar = `risco`).

3. **Item no `MENU`** de `painel/navegacao.py`: `ItemMenu(rota, rotulo, papeis)`.
   Rotas ainda não registradas aparecem desabilitadas, então o item pode entrar antes da tela;
   abrir o endereço direto mostra "em construção" dentro do layout (404 em HTML; `/api/*` e `/parcial/*` seguem em JSON).

4. **Consulta em `painel/dados.py`**: SQL fica só ali; a rota apenas chama a função.

5. **Teste em `tests/`** (ex.: `tests/test_painel_layout.py`): `TestClient(app).get("/exemplo")` e confira o HTML.

Componentes (`c.`): `kpi`, `tabela`, `selo`, `faixa` (`ok`/`aviso`/`erro`), `vazio`, `campo`.
Tons de `kpi` e `selo`: `neutro`, `bom`, `atencao`, `risco`, `azul`.
Células de `tabela` são escapadas; para HTML (ex.: um `c.selo`) o valor já vem marcado como seguro pela macro.
No celular (até 720 px) o menu vira barra inferior só com as telas que existem; com mais de 4, a 5ª vaga vira
"Mais", que abre a lista de telas (`barra_mobile` em `painel/navegacao.py`). `c.tabela` vira cartões; tabela
própria vira cartão com `class="tabela empilha"` e `data-rotulo` em cada `<td>`.
