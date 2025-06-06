#!/usr/bin/env bash
kubectl create namespace deepweb-proxy
kubectl apply -f . -n deepweb-proxy
