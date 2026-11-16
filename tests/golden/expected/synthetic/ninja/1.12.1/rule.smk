# Snakemake Rule for ninja v1.12.1
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule ninja:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/ninja/{sample}.out"
    log:
        "logs/ninja/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/ninja:1.12.1"
    shell:
        """
        ninja \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
