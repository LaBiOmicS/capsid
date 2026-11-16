# Snakemake Rule for bedtools v2.31.1
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule bedtools:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/bedtools/{sample}.out"
    log:
        "logs/bedtools/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/bedtools:2.31.1"
    shell:
        """
        bedtools \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
