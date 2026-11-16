# Workflow integration

Capsid images are ordinary OCI images, so any workflow manager or container runtime can use them.
Capsid also generates starting points for Nextflow, Snakemake and Apptainer.

- [Image references](#image-references)
- [Nextflow](#nextflow)
- [Snakemake](#snakemake)
- [Apptainer and Singularity](#apptainer-and-singularity)
- [HPC clusters](#hpc-clusters)
- [Kubernetes and cloud batch](#kubernetes-and-cloud-batch)
- [Stacks in workflows](#stacks-in-workflows)

---

## Image references

```
ghcr.io/labiomics/capsid/<tool>:<version>          # recipes
ghcr.io/labiomics/capsid/<stack>:<version>         # stacks
ghcr.io/labiomics/capsid/<tool>@sha256:<digest>    # immutable reference
```

Every reference is a multi-architecture index (`linux/amd64`, and `linux/arm64` when the recipe
supports it); the runtime picks the right one automatically. For published pipelines, prefer
digests: a tag can be republished, a digest cannot. `catalog.json` in Capsid Hub lists the
references of every version.

## Nextflow

Generate a module and a configuration:

```bash
capsid export nextflow samtools@1.25 -o modules/local/samtools.nf
capsid export config --target samtools@1.25 -o nextflow.config
```

The module is an nf-core-style DSL2 process: input `tuple val(meta), path(reads)`, a `container`
directive, `task.ext.args` / `task.ext.prefix` hooks, a `versions.yml` output and a `stub` block.
Its script is a generic template (`<tool> $args $reads > <prefix>.out`): edit inputs, outputs and the
command line for your step.

The configuration provides one profile per runtime, which can be combined with the `slurm` executor
profile, and a `withName` block with the image and retry-scaled resources capped by
`process.resourceLimits` (Nextflow 24.04 or newer):

```bash
nextflow run main.nf -profile docker
nextflow run main.nf -profile podman
nextflow run main.nf -profile apptainer
nextflow run main.nf -profile slurm,apptainer
```

| Profile | Settings |
| :--- | :--- |
| `docker` | `docker.enabled`, container runs as the calling user (`-u $(id -u):$(id -g)`) |
| `podman` | `podman.enabled` |
| `apptainer`, `singularity` | enabled with `autoMounts`; add binds for data outside the work directory in `runOptions` |
| `slurm` | `process.executor = 'slurm'`; set your queue and account in the commented lines |

Without the generated files, it is enough to set the image in your own configuration:

```groovy
process {
    withName: 'SAMTOOLS_SORT|SAMTOOLS_INDEX' {
        container = 'ghcr.io/labiomics/capsid/samtools:1.25'
    }
}
```

Every image also contains its module at `/etc/capsid/module.nf`.

## Snakemake

```bash
capsid export snakemake bwa@0.7.19 -o rules/bwa.smk
```

The rule uses `container: "docker://ghcr.io/labiomics/capsid/bwa:0.7.19"`. Snakemake runs containers
through Apptainer/Singularity:

```bash
snakemake --use-apptainer --cores 8                       # Snakemake 8+
snakemake --sdm apptainer --cores 8                       # equivalent, newer syntax
snakemake --sdm apptainer --apptainer-args "-B /scratch"  # extra binds
```

As with Nextflow, edit the rule's `input`, `output` and `shell` for your step. Every image contains
its rule at `/etc/capsid/rule.smk`.

## Apptainer and Singularity

Run directly from the registry (Apptainer converts and caches the image):

```bash
apptainer exec docker://ghcr.io/labiomics/capsid/samtools:1.25 samtools --version
```

or convert once and copy the SIF file to the cluster:

```bash
apptainer pull samtools-1.25.sif docker://ghcr.io/labiomics/capsid/samtools:1.25
apptainer exec --cleanenv -B /scratch samtools-1.25.sif samtools --version
```

`capsid build --apptainer` produces the SIF from a local build, and `capsid export def` writes a
definition file (`Bootstrap: docker`) with a `%test` section taken from the recipe, for sites that
build SIF images from definition files.

Images work with `--cleanenv` and `--containall` because `PATH` is set inside the image, and with any
UID because the tool trees are world-readable and caches go to `/tmp`.

## HPC clusters

- Images are single-node. MPI-enabled tools would need the host MPI ABI and are not a target of
  Capsid recipes today.
- `/scratch`, `/data`, `/work`, `/lustre` and `/shared` exist in every image, so `-B /scratch` works
  even when the runtime cannot create mount points (read-only images, `--containall`).
- For clusters without internet access, pull the SIF files on a login node, or mirror the images to an
  internal registry: `skopeo copy --all docker://ghcr.io/labiomics/capsid/samtools:1.25 docker://registry.example.org/capsid/samtools:1.25`.
  `--all` copies every architecture and preserves the digest, so the image can still be verified
  against the signatures in the original registry.
- Test a pulled image on the cluster with the recipe's own test:
  `capsid test recipes/samtools/1.25/recipe.yaml --engine apptainer --sif samtools-1.25.sif`.

## Kubernetes and cloud batch

Images satisfy common pod security settings out of the box:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  readOnlyRootFilesystem: true      # mount an emptyDir at /tmp
  allowPrivilegeEscalation: false
  capabilities: {drop: [ALL]}
```

The same applies to AWS Batch, Google Batch and Azure Batch job definitions, and to Nextflow's cloud
executors.

## Stacks in workflows

A stack image contains several tools, so one process can pipe them together without staging
intermediate files between containers:

```groovy
process CALL_VARIANTS {
    container 'ghcr.io/labiomics/capsid/variant-calling-stack:1.0.0'
    // ...
    script:
    """
    samtools sort -@ ${task.cpus} ${bam} | bcftools mpileup -f ${ref} - | bcftools call -mv -Oz -o calls.vcf.gz
    """
}
```

Use stacks for tools that are always used together, and separate images otherwise: small images are
cached and reused across pipelines.
