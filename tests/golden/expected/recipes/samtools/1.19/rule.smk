# Snakemake Rule for samtools v1.19
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule samtools:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/samtools/{sample}.out"
    log:
        "logs/samtools/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/samtools:1.19"
    shell:
        """
        samtools \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
