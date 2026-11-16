# Snakemake Rule for multiqc v1.25.1
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule multiqc:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/multiqc/{sample}.out"
    log:
        "logs/multiqc/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/multiqc:1.25.1"
    shell:
        """
        multiqc \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
