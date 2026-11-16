# Snakemake Rule for cutadapt v5.2
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule cutadapt:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/cutadapt/{sample}.out"
    log:
        "logs/cutadapt/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/cutadapt:5.2"
    shell:
        """
        cutadapt \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
