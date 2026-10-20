# Descheduler namespace adoption (#995)

The current pinned seven-object source at b523a9b1 renders identically to main. Add only the existing namespace, preserving its exact four live labels, UID and spec; retain Prune=false/Delete=false. The other seven objects, descheduler image, eviction policy, cluster-wide pod-delete exception and operator-controlled suspend field remain unchanged. The Application stays exact-pinned, manual and non-pruning with RespectIgnoreDifferences for suspend.

Central ownership already records the four Verdify PriorityClasses and the descheduler ClusterRole/Binding. Post-#1006 tier-2 Traefik has no Kubernetes provider, token or grant; do not invent a namespaced Role/Binding to match stale issue prose. Its R18 evidence is zero Kubernetes access.

The Agents companion must allow the existing Namespace kind in the dedicated AppProject and update only this Application’s exact source pin. After those checked sources land, sync only the descheduler Application with prune off; verify namespace tracking, unchanged UID/labels/spec, unchanged seven workload/RBAC/policy objects and unchanged operator pause state. No descheduler run or rebalance is triggered as a test.
