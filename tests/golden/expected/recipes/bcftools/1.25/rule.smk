# Snakemake Rule for bcftools v1.25
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule bcftools:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/bcftools/{sample}.out"
    log:
        "logs/bcftools/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/bcftools:1.25"
    shell:
        """
        bcftools \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
