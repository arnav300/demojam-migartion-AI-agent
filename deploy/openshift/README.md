# DemoJam Copilot on OpenShift

This kustomize base runs the migration engine in namespace `demojam-copilot`. It contains:

| File | What it is |
| --- | --- |
| `namespace.yaml` | Namespace `demojam-copilot` |
| `pvc.yaml` | `copilot-state`: a 10Gi RWO volume for `migration_state.db`, exports, reports and logs. Mounted at `/app/state`. |
| `imagestream.yaml`, `buildconfig.yaml` | Build the image `copilot:latest` in the cluster from this repo's `Containerfile` (branch `demojam/migration-agent`). |
| ConfigMap `copilot-config` | Generated from `config.yaml`, which is a copy of `config/config.yaml`. Mounted at `/app/config`. |
| `job-premigration.yaml` | `aap-migrate premigration-report --source live`. Reads the AAP 2.4 source only. |
| `job-migrate.yaml` | `aap-migrate migrate`: the full prep → export → transform → import run. |
| `secret.example.yaml` | Placeholders only. Not applied. |

Both Jobs are created **suspended**, because the image doesn't exist until the build finishes. You start them by unsuspending them (step 5).

## 1. Create the namespace

```bash
oc new-project demojam-copilot     # or: oc project demojam-copilot
```

## 2. Create the Secret

Never commit real values. A local filled-in copy named `deploy/openshift/secret.yaml` is gitignored.

```bash
oc create secret generic copilot-secrets -n demojam-copilot \
  --from-literal=AAP_SOURCE_URL='https://<aap24-host>/api/v2' \
  --from-literal=AAP_SOURCE_TOKEN='<aap24-token>' \
  --from-literal=AAP_TARGET_URL='https://<aap26-host>/api/controller/v2' \
  --from-literal=AAP_TARGET_TOKEN='<aap26-token>' \
  --from-literal=AI_ENDPOINT='<granite-url>' \
  --from-literal=AI_MODEL='<model-id>' \
  --from-literal=AI_TOKEN='<granite-token>'
```

- **URL format:** the URLs must include the API path, as in `.env.example`.
- **AI off:** leave out the three `AI_*` keys. The Jobs mark them as optional.
- **How the keys reach the tool:** the Jobs pass `AAP_SOURCE_*` to the tool as `SOURCE__URL`/`SOURCE__TOKEN`, and `AAP_TARGET_*` as `TARGET__URL`/`TARGET__TOKEN`. Those are the variable names the tool reads.

## 3. Apply the base and build the image

```bash
oc apply -k deploy/openshift
oc start-build copilot -n demojam-copilot --follow
oc get istag copilot:latest -n demojam-copilot
```

`oc kustomize deploy/openshift` shows what will be applied.

## 4. (If you changed `config/config.yaml`) refresh the ConfigMap

A Job's pod template can't be changed, and re-applying would suspend a running Job. So delete the Jobs first, and only when none is running:

```bash
cp config/config.yaml deploy/openshift/config.yaml
oc delete job --all -n demojam-copilot
oc apply -k deploy/openshift
```

## 5. Run a Job and follow its logs

Pre-migration report (read-only against the source):

```bash
oc patch job copilot-premigration -n demojam-copilot --type=merge -p '{"spec":{"suspend":false}}'
oc logs -f job/copilot-premigration -n demojam-copilot
oc wait --for=condition=complete job/copilot-premigration -n demojam-copilot --timeout=60m
```

Full migration:

```bash
oc patch job copilot-migrate -n demojam-copilot --type=merge -p '{"spec":{"suspend":false}}'
oc logs -f job/copilot-migrate -n demojam-copilot
oc wait --for=condition=complete job/copilot-migrate -n demojam-copilot --timeout=6h
```

The PVC is ReadWriteOnce, so run one Job at a time. If a Job fails, check `oc describe job/<name>`. `backoffLimit` is 0, so a failed Job is not retried.

## 6. Copy the reports off the PVC

You can't copy files out of a finished Job's pod, so start a small helper pod that mounts the same PVC:

```bash
oc apply -n demojam-copilot -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: copilot-files
spec:
  restartPolicy: Never
  containers:
    - name: files
      image: registry.access.redhat.com/ubi9/ubi-minimal
      command: ["sleep", "3600"]
      resources:
        requests: {cpu: 50m, memory: 64Mi}
        limits: {cpu: 200m, memory: 128Mi}
      volumeMounts:
        - {name: state, mountPath: /app/state}
  volumes:
    - name: state
      persistentVolumeClaim: {claimName: copilot-state}
EOF
oc wait --for=condition=Ready pod/copilot-files -n demojam-copilot --timeout=5m
oc rsync -n demojam-copilot copilot-files:/app/state/reports ./copilot-reports
oc rsync -n demojam-copilot copilot-files:/app/state/logs ./copilot-reports
oc delete pod copilot-files -n demojam-copilot
```

`/app/state/migration_state.db` is in the same volume if you need it, for example for `classify-failures`.

## 7. Re-run a Job

Delete the Job and apply the base again. That recreates the Job suspended, so then unsuspend it as in step 5.

```bash
oc delete job copilot-premigration -n demojam-copilot    # or copilot-migrate
oc apply -k deploy/openshift
```

To start from an empty state between demo runs, also delete and recreate the PVC (see `docs/CLUSTER-SETUP.md` step 9):

```bash
oc delete job --all -n demojam-copilot
oc delete pvc copilot-state -n demojam-copilot
oc apply -k deploy/openshift
```
