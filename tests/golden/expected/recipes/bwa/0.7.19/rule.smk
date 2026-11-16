# Snakemake Rule for bwa v0.7.19
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule bwa:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/bwa/{sample}.out"
    log:
        "logs/bwa/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/bwa:0.7.19"
    shell:
        """
        bwa \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
