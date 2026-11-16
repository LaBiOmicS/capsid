"""
Exporter & Workflow Integrator:
Generates production-ready Nextflow DSL2 modules, Nextflow configuration profiles,
Snakemake rules, and Apptainer/Singularity definition files from Capsid recipes.
"""

from typing import Dict, Any, Optional
from capsid.models import Recipe


def export_nextflow_process(recipe: Recipe, registry_prefix: str = "ghcr.io/capsid") -> str:
    """Generate an nf-core compliant Nextflow DSL2 process module."""
    proc_name = recipe.name.upper().replace("-", "_").replace(".", "_")
    tool_cmd = recipe.name

    return f"""// Nextflow DSL2 Module for {recipe.name} v{recipe.version}
// Generated automatically by Capsid Engine (https://github.com/capsid/capsid)

process {proc_name} {{
    tag "$meta.id"
    label 'process_medium'

    container "${{ workflow.containerEngine == 'singularity' || workflow.containerEngine == 'apptainer' ?
        'oras://ghcr.io/capsid-sif/{recipe.name}:{recipe.version}' :
        '{registry_prefix}/{recipe.name}:{recipe.version}' }}"

    input:
    tuple val(meta), path(reads)

    output:
    tuple val(meta), path("*.out"), emit: results
    path "versions.yml"           , emit: versions

    when:
    task.ext.when == null || task.ext.when

    script:
    def args = task.ext.args ?: ''
    def prefix = task.ext.prefix ?: "${{meta.id}}"
    \"\"\"
    # Execute {tool_cmd}
    {tool_cmd} \\
        $args \\
        $reads > "${{prefix}}.out"

    cat <<-END_VERSIONS > versions.yml
    "${{task.process}}":
        {recipe.name}: "{recipe.version}"
        container_engine: "${{workflow.containerEngine}}"
    END_VERSIONS
    \"\"\"

    stub:
    def prefix = task.ext.prefix ?: "${{meta.id}}"
    \"\"\"
    touch "${{prefix}}.out"
    cat <<-END_VERSIONS > versions.yml
    "${{task.process}}":
        {recipe.name}: "{recipe.version}"
    END_VERSIONS
    \"\"\"
}}
"""


def export_nextflow_config(recipe: Optional[Recipe] = None, registry_prefix: str = "ghcr.io/capsid") -> str:
    """Generate universal nextflow.config supporting Docker, Podman, Singularity, Apptainer, and SLURM."""
    specific_process = ""
    if recipe:
        proc_name = recipe.name.upper().replace("-", "_").replace(".", "_")
        specific_process = f"""
    withName: '{proc_name}' {{
        container = '{registry_prefix}/{recipe.name}:{recipe.version}'
        cpus   = {{ check_max( 4 * task.attempt, 'cpus' ) }}
        memory = {{ check_max( 8.GB * task.attempt, 'memory' ) }}
        time   = {{ check_max( 4.h  * task.attempt, 'time' ) }}
    }}"""

    return f"""/*
 * =========================================================================================
 *  Nextflow Configuration Profile for Capsid Containers
 *  Universal execution across Local Workstation, Cloud, and HPC (SLURM / PBS)
 * =========================================================================================
 */

profiles {{
    standard {{
        process.executor = 'local'
    }}

    docker {{
        docker.enabled         = true
        docker.userEmulation   = true
        docker.runOptions      = '-u $(id -u):$(id -g)'
        singularity.enabled    = false
        apptainer.enabled      = false
        podman.enabled         = false
    }}

    podman {{
        podman.enabled         = true
        docker.enabled         = false
        singularity.enabled    = false
        apptainer.enabled      = false
    }}

    apptainer {{
        apptainer.enabled      = true
        apptainer.autoMounts   = true
        apptainer.runOptions   = '-B /scratch,/data,/work,/lustre --cleanenv'
        docker.enabled         = false
        singularity.enabled    = false
        podman.enabled         = false
    }}

    singularity {{
        singularity.enabled    = true
        singularity.autoMounts = true
        singularity.runOptions = '-B /scratch,/data,/work,/lustre --cleanenv'
        docker.enabled         = false
        apptainer.enabled      = false
        podman.enabled         = false
    }}

    slurm {{
        process.executor       = 'slurm'
        process.clusterOptions = '--account=bioinformatics'
        apptainer.enabled      = true
        apptainer.autoMounts   = true
    }}
}}

process {{{specific_process}
    shell = ['/bin/bash', '-euo', 'pipefail']
}}
"""


def export_snakemake_rule(recipe: Recipe, registry_prefix: str = "ghcr.io/capsid") -> str:
    """Generate a production-ready Snakemake rule."""
    rule_name = recipe.name.lower().replace("-", "_").replace(".", "_")
    tool_cmd = recipe.name

    return f"""# Snakemake Rule for {recipe.name} v{recipe.version}
# Generated automatically by Capsid Engine (https://github.com/capsid/capsid)

rule {rule_name}:
    input:
        reads = "data/samples/{{sample}}.fastq.gz"
    output:
        result = "results/{recipe.name}/{{sample}}.out"
    log:
        "logs/{recipe.name}/{{sample}}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://{registry_prefix}/{recipe.name}:{recipe.version}"
    shell:
        \"\"\"
        {tool_cmd} \\
            {{input.reads}} \\
            -o {{output.result}} \\
            > {{log}} 2>&1
        \"\"\"
"""


def export_apptainer_def(recipe: Recipe) -> str:
    """Generate a native Singularity/Apptainer .def definition file."""
    lines = [
        f"Bootstrap: docker",
        f"From: ghcr.io/capsid/{recipe.name}:{recipe.version}",
        "",
        "%labels",
        f"    Author Capsid Project",
        f"    Version {recipe.version}",
        f"    Tool {recipe.name}",
        "",
        "%environment",
        "    export LC_ALL=C.UTF-8",
        "    export LANG=C.UTF-8",
        "    export XDG_CACHE_HOME=/tmp/.cache",
        "",
        "%runscript",
        f'    exec {recipe.name} "$@"',
        "",
        "%test",
        f"    {recipe.test.command}"
    ]
    return "\n".join(lines) + "\n"
