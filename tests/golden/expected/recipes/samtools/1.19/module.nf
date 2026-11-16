// Nextflow DSL2 Module for samtools v1.19
// Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

process SAMTOOLS {
    tag "$meta.id"
    label 'process_medium'

    // Docker/Podman pull it directly; Apptainer/Singularity convert the same OCI image
    container "ghcr.io/labiomics/capsid/samtools:1.19"

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
    # Execute samtools
    samtools \
        $args \
        $reads > "${prefix}.out"

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        samtools: "1.19"
        container_engine: "${workflow.containerEngine}"
    END_VERSIONS
    """

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    touch "${prefix}.out"
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        samtools: "1.19"
    END_VERSIONS
    """
}
