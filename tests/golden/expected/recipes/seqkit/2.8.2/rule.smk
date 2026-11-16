# Snakemake Rule for seqkit v2.8.2
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule seqkit:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/seqkit/{sample}.out"
    log:
        "logs/seqkit/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/seqkit:2.8.2"
    shell:
        """
        seqkit \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
