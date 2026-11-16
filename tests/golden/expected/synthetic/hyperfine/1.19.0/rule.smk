# Snakemake Rule for hyperfine v1.19.0
# Generated automatically by Capsid Engine (https://github.com/LaBiOmicS/capsid)

rule hyperfine:
    input:
        reads = "data/samples/{sample}.fastq.gz"
    output:
        result = "results/hyperfine/{sample}.out"
    log:
        "logs/hyperfine/{sample}.log"
    threads: 4
    resources:
        mem_mb = 8000,
        time = "02:00:00"
    container:
        "docker://ghcr.io/labiomics/capsid/hyperfine:1.19.0"
    shell:
        """
        hyperfine \
            {input.reads} \
            -o {output.result} \
            > {log} 2>&1
        """
