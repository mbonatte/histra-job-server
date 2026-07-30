# Template registry

Place immutable source HRX files here for local Docker Compose use. The filename
without `.hrx` is the JOB template ID. For example:

```text
templates/bridge-base-v1.hrx
```

The corresponding JOB must contain the exact SHA-256 of that file. Production
deployments should mount this directory read-only from a controlled artifact
store. Generated per-attempt HRX files never belong here.
