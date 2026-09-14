# Run Prelum on Kubernetes

The image is the same one [Docker and releases](docker.md) describes. What changes is where the
memory boundary sits: the pod's `limits.memory` is the outer bound, and Prelum's own per-render
limit is the inner one. Both are needed, and they do different jobs.

When a pod exceeds `limits.memory`, the kernel's OOM killer chooses a victim from inside that pod
by `oom_score`. Prelum and every Typst process it forked are candidates. If it picks Prelum itself,
the container restarts: every render in flight dies, the pod drops out of its Service, and the
caller whose template caused the pressure receives the same failure as everyone else.
`PRELUM_MAX_RENDER_MEMORY_BYTES` stops a runaway render before the pod's budget is touched, so the
failure stays a `422` for the one caller responsible while the other renders finish.

The two are distinguishable in the responses, which is the point of setting both. A render that
exceeds its own limit aborts and answers `422 template_compile_failed`: the caller wrote a template
that wanted too much, and nothing is wrong with the deployment. A render killed by the pod's limit
is killed from outside, answers `500 render_failed`, and is logged at `error` so it reaches Sentry
— because that one usually means the pod is under-provisioned for its configured concurrency, and
the caller who received it may have done nothing unusual at all.

Usually, not always. The per-render bound is `RLIMIT_DATA`, which covers the heap and anonymous
mappings: fonts a caller sends in `files` are memory-mapped by Typst outside it, as is scratch space
if `/tmp` is a `medium: Memory` volume, and on kernels older than 4.7 anonymous mappings fall
outside it too. Enough concurrent requests of that shape can push the pod over its limit without any
single render exceeding its own. Nothing in the response can separate that from genuine
under-provisioning, so it is reported as the infrastructure fault it resembles — treat a cluster of
`render_failed` alerts as a sizing question first, and check the payloads second.

## A starting manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prelum
spec:
  replicas: 3
  selector:
    matchLabels:
      app: prelum
  template:
    metadata:
      labels:
        app: prelum
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
      containers:
        - name: prelum
          image: ghcr.io/vrtfinland/prelum:1.0.1
          ports:
            - containerPort: 9870
          env:
            - name: PRELUM_API_TOKEN
              valueFrom:
                secretKeyRef:
                  name: prelum
                  key: api-token
            - name: PRELUM_ENVIRONMENT
              value: production
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
            limits:
              cpu: "2"
              memory: 2Gi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: render-scratch
              mountPath: /tmp
          livenessProbe:
            httpGet:
              path: /health
              port: 9870
          readinessProbe:
            httpGet:
              path: /health
              port: 9870
      volumes:
        - name: render-scratch
          emptyDir:
            sizeLimit: 1Gi
```

## Sizing the pod

The image ships `PRELUM_MAX_RENDER_MEMORY_BYTES=536870912` and `PRELUM_MAX_CONCURRENT_RENDERS=2`.
With the request and output buffers Prelum holds on its own side, that comes to roughly 1.2 GiB of
ceiling, so `limits.memory: 2Gi` leaves room for the interpreter. [Configuration](configuration.md)
sets out the arithmetic in full.

Scale throughput with `replicas`, not with `PRELUM_MAX_CONCURRENT_RENDERS`. Typst is CPU-bound, so
a pod with two cores gains little from more concurrent renders, while every extra one multiplies
the pod's memory ceiling. More replicas also spread the blast radius of a restart.

If you do raise concurrency, raise `limits.memory` in the same commit. Raising one without the
other is the most direct way to reintroduce the pod-wide OOM kill that the per-render limit exists
to prevent.

## Scratch space and the read-only root filesystem

`readOnlyRootFilesystem: true` leaves Prelum nowhere to build a render's temporary project, so the
manifest above mounts an `emptyDir` at `/tmp`, which is where `PRELUM_TEMP_ROOT` points by default.

Leave that volume disk-backed. An `emptyDir` with `medium: Memory` is a tmpfs, and tmpfs pages are
charged to the pod's memory limit — where `RLIMIT_DATA` does not reach them, because it bounds the
Typst process's heap rather than the filesystem. A render writing large inline files to a
memory-backed `/tmp` can therefore OOM the pod with its own memory limit working exactly as
intended. `sizeLimit` bounds the volume independently and is worth setting either way.

If you point `PRELUM_TEMP_ROOT` somewhere other than `/tmp`, keep the path short: the published
`files` key length bound is derived from how much of the operating system's path limit the render
root leaves free, and Prelum refuses to start if a configured root would void it.

## Startup failures are deliberate

Prelum probes the memory-limit wrapper before it serves its first request and exits if the wrapper
cannot honour the configured value. A `CrashLoopBackOff` with `the render memory limit probe
exited ...` in the log means the deployment is misconfigured, not that the service is unhealthy.

This is on purpose. Every way the wrapper can fail ends in the same exit status a failed
compilation produces, so without the probe a broken configuration would answer `422` to every
caller and page nobody. Fix the configuration rather than removing the limit.
