# Snakemake Rule for lz4 v1.10.0
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule lz4:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/lz4/{sample}.out"
    log:
        "logs/lz4/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/lz4:1.10.0"
    shell:
        """
        lz4 \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
