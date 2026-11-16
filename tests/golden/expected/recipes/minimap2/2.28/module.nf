// Nextflow DSL2 Module for minimap2 v2.28
// Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

process MINIMAP2 {
    tag "$meta.id"
    label 'process_medium'

    // Docker/Podman pull it directly; Apptainer/Singularity convert the same OCI image
    container "ghcr.io/labiomics/capsid/minimap2:2.28"

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
    # Execute minimap2
    minimap2 \
        $args \
        $reads > "${prefix}.out"

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        minimap2: "2.28"
        container_engine: "${workflow.containerEngine}"
    END_VERSIONS
    """

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    touch "${prefix}.out"
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        minimap2: "2.28"
    END_VERSIONS
    """
}
