# Snakemake Rule for minimap2 v2.28
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule minimap2:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/minimap2/{sample}.out"
    log:
        "logs/minimap2/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/minimap2:2.28"
    shell:
        """
        minimap2 \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
