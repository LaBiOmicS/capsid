// Nextflow DSL2 Module for lz4 v1.10.0
// Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

process LZ4 {
    tag "$meta.id"
    label 'process_medium'

    // Docker/Podman pull it directly; Apptainer/Singularity convert the same OCI image
    container "ghcr.io/labiomics/capsid/lz4:1.10.0"

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
    # Execute lz4
    lz4 \
        $args \
        $reads > "${prefix}.out"

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        lz4: "1.10.0"
        container_engine: "${workflow.containerEngine}"
    END_VERSIONS
    """

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    touch "${prefix}.out"
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        lz4: "1.10.0"
    END_VERSIONS
    """
}
