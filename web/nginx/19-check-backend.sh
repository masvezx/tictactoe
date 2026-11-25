#!/bin/sh
set -eu

: "${BACKEND_HOST:?Set BACKEND_HOST to the backend HTTPS hostname, without a scheme or path}"
