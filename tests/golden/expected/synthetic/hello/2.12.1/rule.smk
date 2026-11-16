# Snakemake Rule for hello v2.12.1
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule hello:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/hello/{sample}.out"
    log:
        "logs/hello/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/hello:2.12.1"
    shell:
        """
        hello \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
