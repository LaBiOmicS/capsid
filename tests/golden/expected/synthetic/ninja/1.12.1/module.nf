// Nextflow DSL2 Module for ninja v1.12.1
// Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

process NINJA {
    tag "$meta.id"
    label 'process_medium'

    // Docker/Podman pull it directly; Apptainer/Singularity convert the same OCI image
    container "ghcr.io/labiomics/capsid/ninja:1.12.1"

    input:
    tuple val(meta), path(reads)

    output:
    tuple val(meta), path("*.out"), emit: results
    path "versions.yml"           , emit: versions

    when:
    task.ext.when == null || task.ext.when

    script:
    def args = task.ext.args ?: ''
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    # Execute ninja
    ninja \
        $args \
        $reads > "${prefix}.out"

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        ninja: "1.12.1"
        container_engine: "${workflow.containerEngine}"
    END_VERSIONS
    """

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    touch "${prefix}.out"
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        ninja: "1.12.1"
    END_VERSIONS
    """
}
