// Nextflow DSL2 Module for seqkit-bin v2.8.2
// Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

process SEQKIT_BIN {
    tag "$meta.id"
    label 'process_medium'

    // Docker/Podman pull it directly; Apptainer/Singularity convert the same OCI image
    container "ghcr.io/labiomics/capsid/seqkit-bin:2.8.2"

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
    # Execute seqkit-bin
    seqkit-bin \
        $args \
        $reads > "${prefix}.out"

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        seqkit-bin: "2.8.2"
        container_engine: "${workflow.containerEngine}"
    END_VERSIONS
    """

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    touch "${prefix}.out"
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        seqkit-bin: "2.8.2"
    END_VERSIONS
    """
}
