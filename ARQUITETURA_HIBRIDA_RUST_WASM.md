# 🦀 Capsid Híbrido: Visão Arquitetural em Rust & WebAssembly (Wasm)

> **Documento Estratégico de Arquitetura e Especificação Técnica**  
> *Data de registro: 16 de Novembro de 2026*  
> *Autores: Fabiano Menegidio & Comunidade LaBiOmicS*  

---

## 1. Visão Geral e Motivação

O ecossistema **Capsid** foi inicialmente prototipado e validado com sucesso em Python/Typer, comprovando:
1. Imagens multi-stage endurecidas (*rootless* `bio:1000`, `tini` PID 1, montagens HPC universais) até **88% menores** que o BioContainers clássico;
2. Velocidade extrema de resolução utilizando motores em Rust (`uv` para PyPI e `pixi`/`rattler` para Conda);
3. Composição de stacks multi-ferramentas (`capsid compose`);
4. Integração nativa com Nextflow DSL2, Snakemake, Apptainer SIF e proveniência FAIR (RO-Crate 1.1 e SBOM SPDX 2.3).

Para o próximo salto geracional do projeto, redefine-se a ferramenta como uma **plataforma híbrida (Local + Web)** baseada em um núcleo unificado em **Rust** compilado tanto para binário nativo de alta velocidade quanto para **WebAssembly (Wasm)**.

---

## 2. Diagrama de Arquitetura Unificada

```
                          ┌────────────────────────────────────────────────────────┐
                          │                 capsid-core (Rust)                     │
                          │                                                        │
                          │  • Schemas & Validação: `serde`, `serde_yaml`          │
                          │  • Templating Engine: `minijinja`                      │
                          │  • Metadados FAIR: RO-Crate 1.1 & SBOM SPDX 2.3        │
                          │  • Exporters: Nextflow DSL2, Snakemake, Singularity    │
                          │  • Linter de Segurança & Supply Chain Audit            │
                          └───────────────────────────┬────────────────────────────┘
                                                      │
                       ┌──────────────────────────────┴──────────────────────────────┐
                       │                                                             │
                       ▼ (Compilação Nativa via Cargo)                               ▼ (Compilação Wasm via wasm-bindgen)
       ┌──────────────────────────────────────────────┐              ┌──────────────────────────────────────────────┐
       │                 capsid CLI                   │              │                 capsid Web                   │
       │       (HPC, Servidores e Terminais)          │              │          (Navegador / 100% Client-side)      │
       │                                              │              │                                              │
       │  • Binário estático único (~10 MB, zero deps)│              │  • Zero servidor / Custo zero de hosting     │
       │  • Execução Docker BuildKit / Podman         │              │  • Studio Visual de Receitas (Drag & Drop)   │
       │  • Compilação nativa Apptainer (.sif)        │              │  • Visual Stack Composer (Multi-ferramentas) │
       │  • Comando `capsid ui` (web local conectado) │              │  • Live Preview instantâneo (<10ms)          │
       │  • Bindings PyO3 (`pip install capsid`)      │              │  • 1-Click PR para o Capsid Hub via OAuth    │
       └──────────────────────────────────────────────┘              └──────────────────────────────────────────────┘
                                                                                     │
                                                                                     │ Submissões & PRs
                                                                                     ▼
                                                             ┌──────────────────────────────────────────────┐
                                                             │                  capsid-hub                  │
                                                             │         (Fábrica e Armazém Comunitário)      │
                                                             │                                              │
                                                             │  • Git Database das receitas e stacks        │
                                                             │  • GitHub Actions: Multi-arch Cloud Builders │
                                                             │  • Cosign / Sigstore: Assinatura Cripto      │
                                                             │  • GHCR: Distribuição de camadas OCI         │
                                                             │  • GA4GH TRS v2 API pública                  │
                                                             └──────────────────────────────────────────────┘
```

---

## 3. As Duas Faces do Capsid

### Face A: `capsid Web` (Studio no Navegador)
* **Zero Servidor / Zero Backend:** A página é estática (GitHub Pages / Cloudflare Pages). Toda a computação de templates, validação de receitas, formatação de Dockerfiles e checagem de integridade acontece dentro da aba do navegador do usuário via Wasm.
* **Studio Visual de Receitas:** O usuário digita o nome de um pacote (ex: `fastp`). O navegador faz `fetch` direto nas APIs públicas do PyPI ou Bioconda, preenche os campos automaticamente e exibe em abas interativas:
  * O Dockerfile gerado em tempo real;
  * O módulo Nextflow pronto para copiar;
  * A regra Snakemake;
  * O manifesto RO-Crate / SBOM.
* **Visual Stack Composer:** Interface onde o usuário seleciona múltiplas ferramentas em caixas visuais (ex: `samtools` + `bwa` + `cutadapt`), resolve dependências e valida a stack em segundos.
* **Submissão em 1 Clique:** O usuário se autentica com sua conta do GitHub via OAuth. O Wasm monta a receita, cria a branch e abre o Pull Request diretamente contra `LaBiOmicS/capsid-hub`, disparando a esteira de build na nuvem sem precisar usar terminal.

### Face B: `capsid CLI` (Terminal & HPC)
* **Binário Estático Único:** Distribuído via GitHub Releases, `cargo install capsid` ou Homebrew. Não exige Python, `uv` nem gerenciador de pacotes no cluster do usuário.
* **Comando `capsid ui`:** Inicia um micro-servidor HTTP local que abre a mesma interface do Capsid Web no navegador, mas com permissão para disparar builds locais no Docker e Apptainer da máquina.
* **Bindings PyO3:** Permite empacotar a biblioteca como wheel nativo para quem quiser usá-la programaticamente em scripts Python (`import capsid`).

---

## 4. O Papel do `capsid-hub`

O repositório **`capsid-hub`** atua como a infraestrutura de backend descentralizada:
1. **Repositório da Verdade:** Armazena o histórico auditado de todas as receitas (`recipes/`) e stacks (`stacks/`).
2. **Cloud Build Farm:** GitHub Actions atua como compilador distribuído com corredores `linux/amd64` e `linux/arm64`.
3. **Distribuição OCI:** Imagens armazenadas no GitHub Container Registry (`ghcr.io/labiomics/...`).
4. **Catálogo & GA4GH TRS API:** Geração contínua do índice `catalog.json` para orquestradores como Galaxy, Nextflow Tower e Dockstore.

---

## 5. Roteiro de Implementação Passo a Passo (Quando Retornar)

### Etapa 1: A Crate `capsid-core` (Rust)
* [ ] Inicializar workspace Cargo em `capsid/`.
* [ ] Modelar `Recipe`, `StackRecipe`, `BuildConfig` e `TestConfig` com `serde` e validação com `validator`.
* [ ] Portar os templates de Dockerfile, Nextflow e Snakemake para `minijinja`.
* [ ] Implementar geradores de SBOM (SPDX 2.3) e RO-Crate 1.1 em Rust.
* [ ] Criar suite de testes em Rust (`cargo test`).

### Etapa 2: Compilação WebAssembly & Interface Web
* [ ] Configurar `wasm-bindgen` e `wasm-pack` para exportar a API do `capsid-core` para JavaScript/TypeScript.
* [ ] Desenvolver front-end SPA leve (Svelte ou React/Vite) com tema escuro e editor Monaco/Prism.
* [ ] Implementar integração OAuth do GitHub para abertura automática de PRs.
* [ ] Publicar no GitHub Pages (`labiomics.github.io/capsid`).

### Etapa 3: CLI Nativa e Comando `capsid ui`
* [ ] Construir a CLI com `clap` (subcomandos `build`, `test`, `compose`, `lint`, `export`, `ui`).
* [ ] Adicionar micro-servidor local (`axum` ou `tiny_http`) para o comando `capsid ui`.
* [ ] Configurar release multi-plataforma no GitHub Actions (Linux x86_64, Linux aarch64, macOS x86_64, macOS Apple Silicon).

---

## 6. Conclusão

Essa reestruturação coloca o Capsid à frente de alternativas legadas:
* Para o **pesquisador de bancada**: simplicidade absoluta no navegador (WebAssembly).
* Para o **desenvolvedor de pipelines**: módulos Nextflow/Snakemake e RO-Crate imediatos.
* Para o **administrador de HPC**: binário estático ultraleve e imagens SIF endurecidas.
