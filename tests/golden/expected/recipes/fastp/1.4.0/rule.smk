# Snakemake Rule for fastp v1.4.0
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule fastp:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/fastp/{sample}.out"
    log:
        "logs/fastp/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/fastp:1.4.0"
    shell:
        """
        fastp \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
