#!/usr/bin/env bash
kubectl create namespace deepweb-proxy
kubectl apply -f . -n deepweb-proxy
kubectl get secret minio-crawler-tls -n deepweb-proxy -o json \
  | jq 'del(
      .metadata.namespace,
      .metadata.resourceVersion,
      .metadata.uid,
      .metadata.creationTimestamp,
      .metadata.ownerReferences
    )' \
  | kubectl apply -n deepweb-proxy -f -

kubectl get secret ca-key-pair -n cert-manager -o yaml \
  | sed 's/namespace: cert-manager/namespace: deepweb-proxy/' \
  | kubectl apply -f -
