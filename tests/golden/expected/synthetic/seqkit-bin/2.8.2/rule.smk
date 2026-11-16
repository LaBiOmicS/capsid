# Snakemake Rule for seqkit-bin v2.8.2
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule seqkit_bin:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/seqkit-bin/{sample}.out"
    log:
        "logs/seqkit-bin/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/seqkit-bin:2.8.2"
    shell:
        """
        seqkit-bin \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
